"""Daily Scheduler — the main application window.

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

import sys
import json
import platform
import calendar as _cal
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QDialog, QStackedWidget, QMessageBox, QMenu, QSystemTrayIcon,
    QGraphicsOpacityEffect, QSplitter,
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, QRect, QUrl,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation,
)
from PySide6.QtGui import (
    QPainter, QFont, QPixmap, QIcon, QDesktopServices, QKeySequence, QShortcut,
)

import core
import theme
import gcal
import platform_utils
from core import (
    ACTIVITY_TYPES,
    AI_READONLY_TOOLS,
    AI_UNDO_KEEP,
    MANUAL_UNDO_KEEP,
    RELEASES_PAGE,
    _WEEKDAYS,
    _earliest_fit,
    _free_slots,
    allday_cal_events,
    claim_block_alert,
    coerce_end_min,
    end_alert_due,
    find_free_placement,
    fmt_dur,
    fmt_time,
    load_all_activities,
    load_settings,
    min_to_y,
    new_id,
    norm_title,
    now_next_summary,
    parse_calendar_ids,
    parse_hhmm,
    purge_old_alert_marks,
    resolve_date,
    save_all_activities,
    save_settings,
    sequentialize,
    start_alert_due,
    strip_v,
    timed_cal_events,
    week_ahead_lines,
)
from theme import _rgba, _splitter_qss
from gcal import GoogleAuthThread
from platform_utils import is_startup_enabled, play_alert_sound, set_startup
from views import MonthViewWidget, SidebarWidget, TimelineWidget, WeekViewWidget, YearViewWidget
from dialogs import AddActivityDialog, AlertPopup, SettingsDialog, SetupWidget
from aipanel import AIPanel



class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Daily Scheduler {core.APP_VERSION}")
        self.resize(1300, 860)
        self.setMinimumSize(960, 620)

        self._settings     = load_settings()
        self._creds        = None
        self._cal_by_date: Dict[str, List[Dict]] = {}
        self._fetched_keys: set = set()
        self._cal_threads: List[QThread] = []
        self._all_acts:    List[Dict] = load_all_activities()
        self._ai_undo:     List[List[Dict]] = []   # schedule snapshots for AI undo
        self._manual_undo: List[List[Dict]] = []   # v4.0: Ctrl+Z (manual edits + AI turns)
        self._manual_redo: List[List[Dict]] = []   # v4.1: Ctrl+Y restores what Ctrl+Z undid
        self._ai_turn_snapshotted = False
        self._ai_turn_active = False   # a turn is streaming — Undo is locked meanwhile
        self._cur_date:    date = date.today()
        self._view         = "day"
        self._ai_visible   = False
        # notifications (persisted in settings.json)
        self._tray         = None
        self._tray_retry_pending = False   # a tray-availability retry chain is running
        self._notify_act = self._dnd_act = self._startup_act = None   # set in _setup_tray
        self._update_act = None            # tray "update available" item, set in _setup_tray
        self._update_thread = None         # in-flight platform_utils.UpdateCheckThread (one at a time)
        self._update_tag = self._update_url = None   # newest release found, if any
        self._notify_on    = self._settings["notify_on"]
        self._dnd_override = self._settings["dnd_override"]   # break through DND via app-drawn popup
        self._popups:      List[QWidget] = []
        self._notified:    set = set()     # (block_id, startMin) already announced today
        self._notified_ends: set = set()   # (block_id, endMin) end-chimes already fired
        self._notified_day = date.today().isoformat()
        self._really_quit  = False
        self._tray_hinted  = False

        self.setStyleSheet(f"QMainWindow {{ background: {theme.C_BG.name()}; }}")
        self.setWindowIcon(self._make_app_icon())
        # Ctrl+Z / Ctrl+Y — undo/redo schedule edits (manual AND whole AI turns)
        sc = QShortcut(QKeySequence("Ctrl+Z"), self)
        sc.setContext(Qt.WindowShortcut)
        sc.activated.connect(self._manual_undo_last)
        sy = QShortcut(QKeySequence("Ctrl+Y"), self)
        sy.setContext(Qt.WindowShortcut)
        sy.activated.connect(self._manual_redo_last)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setSpacing(0); root.setContentsMargins(0,0,0,0)

        self._stack = QStackedWidget(); root.addWidget(self._stack)

        # Setup page
        self._setup_page = SetupWidget()
        self._setup_page.proceed.connect(self._boot)
        self._stack.addWidget(self._setup_page)

        # App page
        self._app_page = QWidget()
        self._build_app(self._app_page)
        self._stack.addWidget(self._app_page)

        # Auto-boot if creds exist
        if core.CREDS_FILE.exists():
            self._boot()

    # ── App page layout ────────────────────────────────────────────────────
    def _build_app(self, parent):
        lay = QVBoxLayout(parent); lay.setSpacing(0); lay.setContentsMargins(0,0,0,0)
        lay.addWidget(self._build_header())

        body    = QWidget()
        body_l  = QHBoxLayout(body); body_l.setSpacing(0); body_l.setContentsMargins(0, 0, 0, 0)

        # Day view — all-day banner + timeline in a scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {theme.C_BG.name()}; }}")
        self._timeline = TimelineWidget()
        self._timeline.block_create_req.connect(self._on_block_create)
        self._timeline.activity_delete_req.connect(self._delete_activity)
        self._timeline.activity_edit_req.connect(self._edit_activity)
        self._timeline.activity_changed.connect(self._commit_activity_change)
        self._scroll.setWidget(self._timeline)

        self._allday_banner = QLabel()
        self._allday_banner.setWordWrap(True)
        self._allday_banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._allday_banner.hide()
        self._allday_banner.setStyleSheet(
            f"QLabel {{ background: {_rgba(theme.C_INFO, .14)}; color: {theme.C_INFO.name()}; "
            f"border-bottom: 1px solid {_rgba(theme.C_INFO, .32)}; padding: 8px 14px; "
            f"font-size: 12px; font-weight: 500; }}")
        self._day_page = QWidget()
        day_l = QVBoxLayout(self._day_page)
        day_l.setContentsMargins(0, 0, 0, 0); day_l.setSpacing(0)
        day_l.addWidget(self._allday_banner)
        day_l.addWidget(self._scroll, 1)

        # Week / month / year views
        self._week_view = WeekViewWidget()
        self._week_view.day_clicked.connect(self._goto_date)
        self._week_view.block_clicked.connect(self._edit_activity)
        self._month_view = MonthViewWidget()
        self._month_view.day_clicked.connect(self._goto_date)
        self._year_view = YearViewWidget()
        self._year_view.day_clicked.connect(self._goto_date)
        self._year_scroll = QScrollArea()
        self._year_scroll.setWidgetResizable(True)
        self._year_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {theme.C_BG.name()}; }}")
        self._year_scroll.setWidget(self._year_view)

        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(self._day_page)     # 0 — day
        self._view_stack.addWidget(self._week_view)    # 1 — week
        self._view_stack.addWidget(self._month_view)   # 2 — month
        self._view_stack.addWidget(self._year_scroll)  # 3 — year
        self._view_stack.setMinimumWidth(320)

        # Sidebar (Add Activity ↔ Summary sizes are their own vertical splitter)
        self._sidebar = SidebarWidget()
        self._sidebar.split_changed.connect(self._persist_layout_splits)

        # AI Panel (hidden by default) — wired to edit the schedule via tools
        self._ai_panel = AIPanel(self._ai_ctx)
        self._ai_panel.apply_settings(self._settings)
        self._ai_panel.on_model_edited = lambda m: self._update_setting("model", m)
        if self._settings.get("ollama_autostart"):
            QTimer.singleShot(800, self._ai_panel._start_ollama)
        self._ai_panel.execute_tool = self._ai_execute
        self._ai_panel.on_turn_start = self._ai_turn_start
        self._ai_panel.on_turn_end = self._ai_turn_end
        self._ai_panel.on_undo = self._ai_undo_last
        aw = int(self._settings.get("ai_panel_w", 340) or 340)
        self._ai_panel._panel_w = max(220, min(560, aw))

        # Horizontal splitter: calendar | sidebar | AI — drag handles to resize
        self._body_split = QSplitter(Qt.Horizontal)
        self._body_split.setHandleWidth(5)
        self._body_split.setStyleSheet(_splitter_qss())
        self._body_split.addWidget(self._view_stack)
        self._body_split.addWidget(self._sidebar)
        self._body_split.addWidget(self._ai_panel)
        self._body_split.setStretchFactor(0, 1)
        self._body_split.setStretchFactor(1, 0)
        self._body_split.setStretchFactor(2, 0)
        self._body_split.setCollapsible(0, False)  # calendar always visible
        self._body_split.setCollapsible(1, False)  # sidebar always visible
        self._body_split.setCollapsible(2, True)   # AI may collapse to 0 when closed
        # Restore saved sizes (AI starts hidden at width 0)
        saved = self._settings.get("body_split") or []
        if isinstance(saved, list) and len(saved) >= 2:
            cal_w = max(320, int(saved[0]) if int(saved[0]) > 0 else 900)
            side_w = max(170, min(340, int(saved[1]) if int(saved[1]) > 0 else 210))
        else:
            cal_w, side_w = 900, 210
        self._body_split.setSizes([cal_w, side_w, 0])
        self._ai_panel.setMinimumWidth(0)
        self._ai_panel.setMaximumWidth(0)   # no dead pane while closed
        self._ai_panel.hide()
        self._body_split.splitterMoved.connect(self._on_body_split_moved)
        # Sidebar internal split (types vs summary)
        self._sidebar.apply_split_sizes(self._settings.get("sidebar_split") or [])
        body_l.addWidget(self._body_split)
        lay.addWidget(body, 1)

        # Status bar — status text on the left, a hidden "update available" pill
        # on the right (shown only when a newer release is found).
        status_bar = QWidget()
        status_bar.setStyleSheet(
            f"background:{theme.C_SURFACE.name()}; border-top:1px solid {theme.C_BORDER.name()};")
        sb = QHBoxLayout(status_bar); sb.setContentsMargins(0, 0, 10, 0); sb.setSpacing(8)
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setStyleSheet(
            f"color: {theme.C_MUTED.name()}; font-size: 11px; padding: 3px 14px; background: transparent;")
        sb.addWidget(self._status_lbl); sb.addStretch()
        # Live "Now / Next" indicator — always reflects the real current time / today's
        # schedule (independent of the day being viewed), refreshed by _now_timer.
        self._nownext_lbl = QLabel("")
        self._nownext_lbl.setStyleSheet(
            f"color: {theme.C_TEXT.name()}; font-size: 11px; padding: 3px 8px; background: transparent;")
        sb.addWidget(self._nownext_lbl)
        self._update_btn = QPushButton("")
        self._update_btn.setCursor(Qt.PointingHandCursor)
        self._update_btn.setStyleSheet(
            f"QPushButton {{ background:{_rgba(theme.C_ACCENT, .15)}; color:{theme.C_ACCENT.name()};"
            f" border:1px solid {_rgba(theme.C_ACCENT, .5)}; padding:2px 10px; border-radius:{theme.RAD}px;"
            f" font-size:11px; font-weight:bold; }}"
            f"QPushButton:hover {{ background:{_rgba(theme.C_ACCENT, .28)}; }}")
        self._update_btn.clicked.connect(self._open_releases_page)
        self._update_btn.hide()
        sb.addWidget(self._update_btn)
        lay.addWidget(status_bar)

        # Refresh now-line + the Now/Next indicator every 30 s
        self._now_timer = QTimer(self)
        self._now_timer.timeout.connect(self._timeline.update)
        self._now_timer.timeout.connect(self._update_nownext)
        self._now_timer.start(30_000)

        # Tray icon + block-start notifications
        self._setup_tray()
        self._notify_timer = QTimer(self)
        self._notify_timer.timeout.connect(self._check_block_starts)
        self._notify_timer.start(20_000)   # check every 20 s

        # Update check — once shortly after launch, then daily. Fails silently
        # (and 404s harmlessly while the repo is private).
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._check_for_update)
        self._update_timer.start(24 * 3600 * 1000)   # once a day
        QTimer.singleShot(9000, self._check_for_update)

        self._refresh_view()

    def _build_header(self) -> QWidget:
        hdr = QWidget(); hdr.setFixedHeight(56)
        hdr.setStyleSheet(
            f"background:{theme.C_SURFACE.name()}; border-bottom:1px solid {theme.C_BORDER.name()};")
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(18, 0, 14, 0); hl.setSpacing(8)

        def hbtn(text, checked=False):
            b = QPushButton(text)
            b.setCheckable(checked)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background:{theme.C_SURF2.name()}; border:1px solid {theme.C_BORDER.name()};
                color:{theme.C_MUTED.name()}; padding:6px 14px; border-radius:{theme.RAD}px; font-size:12px; }}
                QPushButton:hover {{ color:{theme.C_TEXT.name()}; border-color:{theme.C_BORDER2.name()};
                background:{_rgba(theme.C_TEXT, .04)}; }}
                QPushButton:checked {{ background:{_rgba(theme.C_ACCENT, .16)};
                border-color:{_rgba(theme.C_ACCENT, .55)}; color:{theme.C_ACCENT.name()}; font-weight:600; }}
            """)
            return b

        def icon_btn(text, tip, font_px=16):
            # Fixed-width + large padding clips emoji/glyphs (‹ › ⚙) to invisibility.
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(36, 32)
            b.setToolTip(tip)
            b.setStyleSheet(f"""
                QPushButton {{ background:{theme.C_SURF2.name()}; border:1px solid {theme.C_BORDER.name()};
                color:{theme.C_TEXT.name()}; padding:0; border-radius:{theme.RAD}px;
                font-size:{font_px}px; font-weight:600; }}
                QPushButton:hover {{ color:{theme.C_ACCENT.name()}; border-color:{theme.C_ACCENT.name()};
                background:{_rgba(theme.C_ACCENT, .12)}; }}
                QPushButton:pressed {{ background:{_rgba(theme.C_ACCENT, .22)}; }}
            """)
            return b

        logo = QLabel("◈  Daily Scheduler")
        logo.setStyleSheet(
            f"font-size:15px; font-weight:700; color:{theme.C_ACCENT.name()}; letter-spacing:0.2px;")
        hl.addWidget(logo)
        ver = QLabel(f"v{core.APP_VERSION}")
        ver.setStyleSheet(f"color:{theme.C_MUTED.name()}; font-size:10px; padding-top:4px;")
        hl.addWidget(ver)

        prev_b = icon_btn("‹", "Previous", font_px=18)
        prev_b.clicked.connect(lambda: self._nav(-1))
        today_b = hbtn("Today")
        today_b.clicked.connect(lambda: self._goto_date(date.today()))
        next_b = icon_btn("›", "Next", font_px=18)
        next_b.clicked.connect(lambda: self._nav(1))
        hl.addWidget(prev_b); hl.addWidget(today_b); hl.addWidget(next_b)

        self._date_lbl = QLabel(datetime.now().strftime("%A, %B %d, %Y"))
        self._date_lbl.setStyleSheet(f"color:{theme.C_TEXT.name()}; font-size:13px; font-weight:bold;")
        hl.addWidget(self._date_lbl); hl.addStretch()

        self._view_btns = {}
        for vid, vlbl in [("day", "Day"), ("week", "Week"),
                          ("month", "Month"), ("year", "Year")]:
            b = hbtn(vlbl, checked=True)
            b.setChecked(vid == "day")
            b.clicked.connect(lambda _, v=vid: self._set_view(v))
            self._view_btns[vid] = b
            hl.addWidget(b)

        self._ai_btn = hbtn("AI", checked=True)
        self._ai_btn.clicked.connect(self._toggle_ai)
        hl.addWidget(self._ai_btn)

        self._refresh_btn = hbtn("↺ Refresh")
        self._refresh_btn.clicked.connect(self._refresh_cal)
        hl.addWidget(self._refresh_btn)

        settings_b = icon_btn("⚙", "Settings", font_px=15)
        settings_b.clicked.connect(self._open_settings)
        hl.addWidget(settings_b)

        self._auth_btn = QPushButton("Connect Google")
        self._auth_btn.setStyleSheet(f"""
            QPushButton {{ background:{theme.C_ACCENT.name()}; color:{theme.C_ON_ACCENT.name()}; padding:5px 13px;
            border-radius:{theme.RAD}px; font-size:12px; border:none; }}
            QPushButton:hover {{ background:{theme.C_ACCENT2.name()}; }}
        """)
        self._auth_btn.clicked.connect(self._auth_google)
        hl.addWidget(self._auth_btn)

        return hdr

    # ── Boot ───────────────────────────────────────────────────────────────
    def _boot(self):
        self._stack.setCurrentIndex(1)
        if core.CREDS_FILE.exists():
            self._auth_google()

    def _auth_google(self):
        self._set_status("Connecting to Google Calendar…")
        self._auth_t = GoogleAuthThread()
        self._auth_t.done.connect(self._on_auth)
        self._auth_t.error.connect(lambda e: self._set_status(f"Auth error: {e}", True))
        self._auth_t.start()

    def _on_auth(self, creds):
        self._creds = creds
        self._auth_btn.setText("● Connected")
        self._auth_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {theme.C_BORDER.name()};
            color: {theme.C_OK.name()}; padding: 5px 13px; border-radius: {theme.RAD}px; font-size: 12px; }}
        """)
        self._set_status("Google connected. Fetching events…")
        self._refresh_cal()

    def _refresh_cal(self):
        if not self._creds:
            self._set_status("Not connected to Google Calendar."); return
        self._fetched_keys.clear()
        self._cal_by_date.clear()
        self._ensure_cal_for_view()
        self._prefetch_ai_months()   # warm this + next month for the AI's week-ahead context

    @staticmethod
    def _month_range(y: int, m: int):
        """(_fetched_keys key, start, end-exclusive) covering calendar month (y, m)."""
        return (f"m{y}-{m}", date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1))

    def _fetch_ranges(self, ranges):
        """Start a gcal.CalFetchThread for each (key, start, end) range not already fetched."""
        for key, start, end in ranges:
            if key in self._fetched_keys:
                continue
            self._fetched_keys.add(key)
            self._set_status("Fetching calendar…")
            t = gcal.CalFetchThread(
                self._creds, start, end,
                calendar_ids=parse_calendar_ids(self._settings.get("calendar_ids", "primary")))
            t.done.connect(self._on_cal)
            t.error.connect(lambda e, k=key: (self._fetched_keys.discard(k),
                                              self._set_status(e, True)))
            # Partial failure: good calendars synced (key stays fetched) — just warn.
            t.warn.connect(lambda m: self._set_status(m, True))
            t.finished.connect(lambda t=t: t in self._cal_threads and self._cal_threads.remove(t))
            self._cal_threads.append(t)
            t.start()

    def _ensure_cal_for_view(self):
        """Fetch Google events covering the visible range, once per range."""
        if not self._creds:
            return
        d = self._cur_date
        if self._view == "year":
            ranges = [(f"y{d.year}", date(d.year, 1, 1), date(d.year + 1, 1, 1))]
        else:
            # month key(s) covering the visible range — a week can straddle two months
            if self._view == "week":
                monday = d - timedelta(days=d.weekday())
                months = {(dd.year, dd.month) for dd in (monday, monday + timedelta(days=6))}
            else:
                months = {(d.year, d.month)}
            ranges = [self._month_range(y, m) for y, m in sorted(months)]
        self._fetch_ranges(ranges)

    def _prefetch_ai_months(self):
        """Warm the calendar cache for this + next month so the AI's next-7-days context
        (see week_ahead_lines) is populated without a per-view fetch race, and forward
        navigation is instant. A 7-day window spans at most two months, so this + next
        month always covers it. Reuses the per-view fetch keys, so nothing is fetched
        twice."""
        if not self._creds:
            return
        today = date.today()
        nxt   = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        self._fetch_ranges([self._month_range(today.year, today.month),
                            self._month_range(nxt.year,   nxt.month)])

    def _on_cal(self, by_date: dict):
        self._cal_by_date.update(by_date)
        self._refresh_view()
        n = sum(len(v) for v in by_date.values())
        self._set_status(f"Synced {n} event{'s' if n != 1 else ''}")

    # ── Per-day data accessors ─────────────────────────────────────────────
    def _day_cal(self, d: Optional[date] = None) -> List[Dict]:
        d = d or self._cur_date
        return self._cal_by_date.get(d.isoformat(), [])

    def _cal_intervals(self, ds: str):
        """(start, end) minute pairs of TIMED read-only calendar events on date `ds`,
        passed to sequentialize() so editable blocks get pushed off meetings.
        All-day events are informational only — they must not occupy the whole day."""
        return [(e["startMin"], e["endMin"])
                for e in timed_cal_events(self._cal_by_date.get(ds, []))]

    def _day_acts(self, d: Optional[date] = None) -> List[Dict]:
        ds = (d or self._cur_date).isoformat()
        return [a for a in self._all_acts if a.get("date") == ds]

    def _free_gaps(self, ds: str, after=core.DAY_START, before=core.DAY_END):
        """Open intervals on `ds` not occupied by editable blocks OR timed calendar
        events, within [after, before]. All-day events do not consume free time.
        Returns [(start, end)] in minutes."""
        occ = [(a["startMin"], a["endMin"]) for a in self._all_acts if a.get("date") == ds] + \
              [(e["startMin"], e["endMin"])
               for e in timed_cal_events(self._cal_by_date.get(ds, []))]
        return [(s, e) for s, e in _free_slots(occ, after, before) if e > s]

    def _select_acts(self, ds: str, title=None, at=None) -> List[Dict]:
        """Select user blocks on date `ds` by fuzzy title and/or start time `at`
        (24h HH:MM). With `at`, matches the block starting at that time, or — if none
        starts exactly then — the block that covers that minute. Combining title+at
        narrows to blocks that satisfy both. Raises ValueError on a bad time."""
        pool = [a for a in self._all_acts if a.get("date") == ds]
        q = norm_title(title) if title else None
        if q is not None:
            pool = [a for a in pool
                    if q in norm_title(a.get("title", ""))
                    or norm_title(a.get("title", "")) in q]
        if at:
            tm = parse_hhmm(str(at))
            exact = [a for a in pool if a["startMin"] == tm]
            pool = exact if exact else [a for a in pool
                                        if a["startMin"] <= tm < a["endMin"]]
        return pool

    # ── Navigation ─────────────────────────────────────────────────────────
    def _set_view(self, v: str):
        self._view = v
        for k, b in self._view_btns.items():
            b.setChecked(k == v)
        self._view_stack.setCurrentIndex({"day": 0, "week": 1, "month": 2, "year": 3}[v])
        self._ensure_cal_for_view()
        self._refresh_view()
        # No opacity fade on painted views — QGraphicsOpacityEffect re-rasterizes the
        # full timeline/week grid every frame and stutters badly.

    def _goto_date(self, d: date):
        self._cur_date = d
        if self._view != "day":
            self._set_view("day")
        else:
            self._ensure_cal_for_view()
            self._refresh_view()

    def _nav(self, step: int):
        d = self._cur_date
        if self._view == "day":
            self._cur_date = d + timedelta(days=step)
        elif self._view == "week":
            self._cur_date = d + timedelta(days=7 * step)
        elif self._view == "month":
            m = d.month + step
            y = d.year + (m - 1) // 12
            m = (m - 1) % 12 + 1
            self._cur_date = date(y, m, min(d.day, _cal.monthrange(y, m)[1]))
        else:
            y = d.year + step
            self._cur_date = date(y, d.month, min(d.day, _cal.monthrange(y, d.month)[1]))
        self._ensure_cal_for_view()
        self._refresh_view()

    # ── View refresh ───────────────────────────────────────────────────────
    def _refresh_view(self):
        self._update_nownext()   # keep Now/Next current after any edit/nav/fetch
        d = self._cur_date
        if self._view == "day":
            self._date_lbl.setText(d.strftime("%A, %B %d, %Y"))
            cal_ev = self._day_cal()
            acts   = self._day_acts()
            self._timeline.set_data(cal_ev, acts, d)
            self._sidebar.update_summary(cal_ev, acts)
            # All-day Google events (holidays, due dates) — banner above the timeline
            ads = allday_cal_events(cal_ev)
            if ads:
                titles = " · ".join(e.get("title") or "(no title)" for e in ads)
                self._allday_banner.setText(f"All day  ·  {titles}")
                self._allday_banner.setToolTip(
                    "\n".join(e.get("title") or "(no title)" for e in ads))
                self._allday_banner.show()
            else:
                self._allday_banner.hide()
            # Only re-center the timeline when the shown day actually changes (initial
            # load or navigation). On an in-place refresh — an edit, drag, or calendar
            # fetch — keep the user's scroll position instead of jumping back to now/top.
            if getattr(self, "_last_day_shown", None) != d:
                self._last_day_shown = d
                if d == date.today():
                    now_min = datetime.now().hour * 60 + datetime.now().minute
                    y = max(0, min_to_y(max(now_min - 60, core.DAY_START)))
                else:
                    y = 0
                QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(y))
        elif self._view == "week":
            monday = d - timedelta(days=d.weekday())
            sunday = monday + timedelta(days=6)
            if monday.month == sunday.month:
                lbl = f"{monday.strftime('%B')} {monday.day} – {sunday.day}, {sunday.year}"
            elif monday.year != sunday.year:   # New-Year week: spell out both years
                lbl = (f"{monday.strftime('%b')} {monday.day}, {monday.year} – "
                       f"{sunday.strftime('%b')} {sunday.day}, {sunday.year}")
            else:
                lbl = (f"{monday.strftime('%b')} {monday.day} – "
                       f"{sunday.strftime('%b')} {sunday.day}, {sunday.year}")
            self._date_lbl.setText(lbl)
            days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
            acts = {ds: [a for a in self._all_acts if a.get("date") == ds] for ds in days}
            cal  = {ds: self._cal_by_date.get(ds, []) for ds in days}
            self._week_view.set_week(monday, acts, cal)
        elif self._view == "month":
            self._date_lbl.setText(d.strftime("%B %Y"))
            ev: Dict[str, List[Dict]] = {}
            for ds, lst in self._cal_by_date.items():
                if lst:
                    ev.setdefault(ds, []).extend(lst)
            for a in self._all_acts:
                ev.setdefault(a.get("date", ""), []).append(a)
            self._month_view.set_month(d.year, d.month, ev)
        else:
            self._date_lbl.setText(str(d.year))
            busy = {k for k, v in self._cal_by_date.items() if v} | \
                   {a.get("date") for a in self._all_acts}
            self._year_view.set_year(d.year, busy)

    # ── Activity actions ───────────────────────────────────────────────────
    def _manual_snapshot(self):
        """Push current schedule so Ctrl+Z can restore it after a manual edit.
        A new edit forks history — whatever Ctrl+Y could restore is gone."""
        self._manual_undo.append([dict(a) for a in self._all_acts])
        del self._manual_undo[:-MANUAL_UNDO_KEEP]
        self._manual_redo.clear()

    def _manual_undo_last(self):
        """Ctrl+Z: restore the schedule to before the last edit — a manual
        create/edit/drag/delete OR a whole AI turn (AI turns snapshot here too,
        so Ctrl+Z after 'plan my day' undoes the plan, not your edit before it).
        The undone state goes to the redo stack — Ctrl+Y brings it back."""
        if self._ai_turn_active:
            self._set_status("Wait for the assistant to finish before undoing.")
            return
        if not self._manual_undo:
            self._set_status("Nothing to undo.")
            return
        self._manual_redo.append([dict(a) for a in self._all_acts])
        del self._manual_redo[:-MANUAL_UNDO_KEEP]
        self._all_acts = self._manual_undo.pop()
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()
        self._set_status("Undid last edit. (Ctrl+Y to redo)")

    def _manual_redo_last(self):
        """Ctrl+Y: restore what the last Ctrl+Z undid."""
        if self._ai_turn_active:
            self._set_status("Wait for the assistant to finish before redoing.")
            return
        if not self._manual_redo:
            self._set_status("Nothing to redo.")
            return
        # Direct append (not _manual_snapshot — that would clear the redo stack)
        self._manual_undo.append([dict(a) for a in self._all_acts])
        del self._manual_undo[:-MANUAL_UNDO_KEEP]
        self._all_acts = self._manual_redo.pop()
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()
        self._set_status("Redid. (Ctrl+Z to undo again)")

    def _on_block_create(self, s, e):
        dlg = AddActivityDialog(s, e, self._sidebar.selected_type,
                                self._cur_date.isoformat(), parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.result_activity:
            self._manual_snapshot()
            self._all_acts.append(dlg.result_activity)
            save_all_activities(self._all_acts)
            self._ai_undo_invalidate()
            self._refresh_view()

    def _edit_activity(self, aid):
        act = next((a for a in self._all_acts if a["id"] == aid), None)
        if not act:
            return
        dlg = AddActivityDialog(act["startMin"], act["endMin"], act["type"],
                                existing=act, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._manual_snapshot()
        if dlg.result_deleted:
            self._all_acts = [a for a in self._all_acts if a["id"] != aid]
        elif dlg.result_activity:
            self._all_acts = [dlg.result_activity if a["id"] == aid else a
                              for a in self._all_acts]
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()

    def _commit_activity_change(self, aid, start, end):
        """Apply a drag move/resize to an existing block."""
        self._manual_snapshot()
        for a in self._all_acts:
            if a["id"] == aid:
                a["startMin"] = max(core.DAY_START, int(start))
                a["endMin"]   = min(core.DAY_END, max(int(end), a["startMin"] + self._timeline.SNAP))
                break
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()

    def _delete_activity(self, aid):
        self._manual_snapshot()
        self._all_acts = [a for a in self._all_acts if a["id"] != aid]
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()

    # ── Layout splitters (calendar | sidebar | AI, and types | summary) ────
    def _on_body_split_moved(self, *_):
        sizes = self._body_split.sizes()
        if self._ai_visible and len(sizes) >= 3 and sizes[2] > 0:
            self._ai_panel._panel_w = sizes[2]
        self._persist_layout_splits()

    def _persist_layout_splits(self):
        """Schedule a debounced save of the section sizes. splitterMoved fires per
        pixel step during a drag — writing settings.json each time is dozens of
        synchronous disk writes per second; one write after the drag settles is
        enough. Flushed immediately on close/quit."""
        if not hasattr(self, "_split_save_timer"):
            self._split_save_timer = QTimer(self)
            self._split_save_timer.setSingleShot(True)
            self._split_save_timer.setInterval(400)
            self._split_save_timer.timeout.connect(self._persist_layout_splits_now)
        self._split_save_timer.start()   # restart → fires 400 ms after the LAST move

    def _persist_layout_splits_now(self):
        """Remember section sizes so the next launch looks the same."""
        try:
            sizes = list(self._body_split.sizes())
            # Always store AI preferred width even when the panel is closed (0)
            if self._ai_visible and len(sizes) >= 3 and sizes[2] > 0:
                self._settings["ai_panel_w"] = sizes[2]
            elif not self._ai_visible:
                # Keep last open width; body_split[2] is 0 while hidden
                sizes = [sizes[0], sizes[1],
                         int(self._settings.get("ai_panel_w", 340) or 340)]
            self._settings["body_split"] = sizes
            self._settings["sidebar_split"] = self._sidebar.split_sizes()
            save_settings(self._settings)
        except Exception:
            pass

    # ── AI panel ───────────────────────────────────────────────────────────
    def _toggle_ai(self):
        """Show/hide the AI panel inside the body splitter (drag the handle to resize)."""
        self._ai_visible = not self._ai_visible
        self._ai_btn.setChecked(self._ai_visible)
        panel = self._ai_panel
        if getattr(self, "_ai_slide", None) is not None:
            try:
                self._ai_slide.stop()
            except Exception:
                pass
            self._ai_slide = None
        sizes = list(self._body_split.sizes())
        cal = sizes[0] if sizes else 900
        side = sizes[1] if len(sizes) > 1 else 210
        if self._ai_visible:
            aw = int(self._settings.get("ai_panel_w", 340) or 340)
            aw = max(220, min(560, aw, getattr(panel, "_panel_w", aw)))
            panel.setMinimumWidth(220)
            panel.setMaximumWidth(560)
            self._body_split.setCollapsible(2, True)
            panel.show()
            # Steal width from the calendar; keep sidebar as-is
            self._body_split.setSizes([max(320, cal - aw), side, aw])
            eff = QGraphicsOpacityEffect(panel)
            panel.setGraphicsEffect(eff)
            eff.setOpacity(0.0)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(140)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutQuad)
            def _clear():
                panel.setGraphicsEffect(None)
            anim.finished.connect(_clear)
            self._ai_slide = anim
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            self._persist_layout_splits()
        else:
            if len(sizes) >= 3 and sizes[2] > 0:
                self._settings["ai_panel_w"] = sizes[2]
                panel._panel_w = sizes[2]
            # Collapse fully so dragging the right handle can't open a dead pane
            panel.setMinimumWidth(0)
            panel.setMaximumWidth(0)
            self._body_split.setCollapsible(2, True)
            panel.hide()
            panel.setGraphicsEffect(None)
            # Return AI width to the calendar
            self._body_split.setSizes([cal + (sizes[2] if len(sizes) > 2 else 0), side, 0])
            self._persist_layout_splits()

    def _ai_ctx(self):
        now = datetime.now()
        today = date.today()
        return {"cal_events": self._day_cal(),
                "activities": self._day_acts(),
                "week_ahead": week_ahead_lines(self._cal_by_date, today),
                "view_date":  self._cur_date.isoformat(),
                "today":      today.isoformat(),
                "weekday":    now.strftime("%A"),
                "now_min":    now.hour * 60 + now.minute,
                "viewing_today": self._cur_date == today}

    # ── AI undo ──────────────────────────────────────────────────────────────
    def _ai_turn_start(self):
        """A new AI turn begins — allow one fresh undo snapshot, and lock Undo
        until the turn finishes (undoing mid-turn would let the turn's later
        tool rounds mutate the restored schedule with no snapshot)."""
        self._ai_turn_snapshotted = False
        self._ai_turn_active = True
        self._update_undo_state()

    def _ai_turn_end(self):
        """The turn finished (final text, error, round limit, or Stop). If its
        tools all failed or changed nothing, drop the do-nothing snapshot so
        the Undo button always maps to a real change."""
        self._ai_turn_active = False
        if (self._ai_turn_snapshotted and self._ai_undo
                and self._all_acts == self._ai_undo[-1]):
            self._ai_undo.pop()
            # The turn also pushed this snapshot onto the Ctrl+Z history — drop
            # that copy too, or a do-nothing turn leaves a no-op undo point.
            if self._manual_undo and self._manual_undo[-1] == self._all_acts:
                self._manual_undo.pop()
            self._ai_turn_snapshotted = False
        self._update_undo_state()

    def _ai_snapshot_before(self, name: str):
        """Before the first schedule-changing tool of the turn, snapshot the
        current schedule so the whole turn can be undone as a single step."""
        if name in AI_READONLY_TOOLS or self._ai_turn_snapshotted:
            return
        self._ai_undo.append([dict(a) for a in self._all_acts])
        del self._ai_undo[:-AI_UNDO_KEEP]
        # Also feed the Ctrl+Z history (v4.1): without this, Ctrl+Z after an AI
        # turn jumped back to before the last MANUAL edit, silently discarding
        # everything the AI had built since — with no way to get it back.
        self._manual_snapshot()
        self._ai_turn_snapshotted = True
        self._update_undo_state()

    def _ai_undo_last(self):
        """Restore the schedule to before the assistant's most recent change."""
        if self._ai_turn_active or not self._ai_undo:
            return
        # Feed the redo stack so Ctrl+Y can bring the AI's change back, exactly
        # like a Ctrl+Z would.
        self._manual_redo.append([dict(a) for a in self._all_acts])
        del self._manual_redo[:-MANUAL_UNDO_KEEP]
        self._all_acts = self._ai_undo.pop()
        # The AI turn pushed the same snapshot onto the Ctrl+Z history; drop that
        # duplicate or the next Ctrl+Z is a no-op "restore" to the current state.
        if self._manual_undo and self._manual_undo[-1] == self._all_acts:
            self._manual_undo.pop()
        self._ai_turn_snapshotted = False   # a post-undo tool round must re-snapshot
        save_all_activities(self._all_acts)
        self._refresh_view()
        self._update_undo_state()
        self._set_status("Undid the assistant's last change. (Ctrl+Y to redo)")

    def _ai_undo_invalidate(self):
        """A manual edit changed the schedule — the snapshots no longer represent
        'current state minus the AI change', and restoring one would silently
        wipe the user's own work. Drop the stack."""
        if self._ai_undo:
            self._ai_undo.clear()
            self._update_undo_state()

    def _update_undo_state(self):
        if getattr(self, "_ai_panel", None):
            self._ai_panel.set_undo_enabled(bool(self._ai_undo)
                                            and not self._ai_turn_active)

    def _ai_execute(self, name: str, args: Dict) -> str:
        """Run one AI tool call against the schedule. Returns a result string
        that is shown in chat AND fed back to the model."""
        try:
            self._ai_snapshot_before(name)   # capture undo point before a change
            ds = resolve_date(args.get("date"), self._cur_date)
            if ds is None:
                return (f"Error: couldn't understand the date "
                        f"'{args.get('date')}'. Use Month/Day like 6/14, or 'tomorrow'.")

            if name == "add_block":
                sm = parse_hhmm(str(args["start"]))
                em = coerce_end_min(sm, parse_hhmm(str(args["end"])))
                if em <= sm:
                    return "Error: end must be after start (use 24:00 for end of day)."
                tid = str(args.get("type", "study"))
                at  = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                title = str(args.get("title") or f"{at['icon']} {at['label']}")
                day_blocks = [b for b in self._all_acts if b.get("date") == ds] + \
                             timed_cal_events(self._cal_by_date.get(ds, []))
                dur    = em - sm
                placed = find_free_placement(day_blocks, sm, dur)
                if placed is None:
                    return (f"Error: no free {fmt_dur(dur)} slot left on {ds} — the day "
                            f"is full. Rebuild it with replace_day, or use a shorter block.")
                note = ""
                if placed != sm:
                    note = (f" ({fmt_time(sm)} was taken — placed at the nearest free "
                            f"slot instead.)")
                sm, em = placed, placed + dur
                self._all_acts.append({
                    "id": new_id(), "date": ds, "startMin": sm, "endMin": em,
                    "type": at["id"], "color": at["color"], "title": title,
                })
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Added '{title}' on {ds}, {fmt_time(sm)}–{fmt_time(em)}.{note}"

            if name == "delete_block":
                title = args.get("title")
                at    = args.get("at")
                if not (title and str(title).strip()) and not at:
                    return ("Error: give a title and/or a time ('at'). To remove every "
                            "block on a date, call clear_day instead.")
                try:
                    hits = self._select_acts(ds, title, at)
                except ValueError as ex:
                    return f"Error: {ex}"
                if not hits:
                    avail = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}"
                                      for a in sorted(self._day_acts(),
                                                      key=lambda x: x["startMin"])) or "none"
                    sel = (f"title '{title}'" if title else "") + \
                          (f" at {at}" if at else "")
                    return (f"No editable block matching {sel.strip()} on {ds}. "
                            f"Blocks that day: {avail}.")
                for a in hits:
                    self._all_acts.remove(a)
                save_all_activities(self._all_acts)
                self._refresh_view()
                return "Deleted: " + ", ".join(
                    f"'{a['title']}' {fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}"
                    for a in hits)

            if name == "move_block":
                title = args.get("title")
                at    = args.get("at")
                if not (title and str(title).strip()) and not at:
                    return "Error: identify the block by 'title' and/or its time ('at')."
                try:
                    hits = self._select_acts(ds, title, at)
                except ValueError as ex:
                    return f"Error: {ex}"
                if not hits:
                    avail = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}"
                                      for a in sorted(self._day_acts(),
                                                      key=lambda x: x["startMin"])) or "none"
                    return (f"No editable block matching that on {ds}. "
                            f"Blocks that day: {avail}.")
                if len(hits) > 1:
                    listing = "; ".join(f"'{a['title']}' at {fmt_time(a['startMin'])}"
                                        for a in sorted(hits, key=lambda x: x["startMin"])[:5])
                    return (f"Ambiguous — {len(hits)} blocks match: {listing}. "
                            f"Add 'at' with the exact start time to pick one.")
                a = hits[0]
                orig = (a["startMin"], a["endMin"], a.get("date"), a.get("title"))
                old_dur = a["endMin"] - a["startMin"]
                if args.get("start"):
                    a["startMin"] = parse_hhmm(str(args["start"]))
                    if not args.get("end"):   # only start given → keep the duration
                        a["endMin"] = min(a["startMin"] + old_dur, core.DAY_END)
                if args.get("end"):
                    a["endMin"] = coerce_end_min(a["startMin"], parse_hhmm(str(args["end"])))
                if args.get("new_date"):
                    nd = resolve_date(args["new_date"], self._cur_date)
                    if nd is None:
                        return f"Error: couldn't understand new_date '{args['new_date']}'."
                    a["date"] = nd
                if args.get("new_title"):
                    a["title"] = str(args["new_title"]).strip()
                if a["endMin"] <= a["startMin"]:
                    a["endMin"] = min(a["startMin"] + 60, core.DAY_END)
                # Keep it conflict-free: if the requested slot overlaps another block or a
                # meeting, relocate to the nearest free slot (like add_block) rather than
                # leaving an overlap. Revert cleanly if the day has no room at all.
                dur = a["endMin"] - a["startMin"]
                day_blocks = [b for b in self._all_acts
                              if b is not a and b.get("date") == a["date"]] + \
                             timed_cal_events(self._cal_by_date.get(a["date"], []))
                placed = find_free_placement(day_blocks, a["startMin"], dur)
                if placed is None:
                    a["startMin"], a["endMin"], a["date"], a["title"] = orig
                    return (f"Error: no free {fmt_dur(dur)} slot on {a['date']} to move "
                            f"'{a['title']}' into — that day is full. Free something up first, "
                            f"or use replace_day to rebuild it.")
                note = ""
                if placed != a["startMin"]:
                    note = (f" ({fmt_time(a['startMin'])} was taken — placed at the nearest "
                            f"free slot instead.)")
                a["startMin"], a["endMin"] = placed, placed + dur
                save_all_activities(self._all_acts)
                self._refresh_view()
                return (f"Moved '{a['title']}' to {a['date']}, "
                        f"{fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}.{note}")

            if name == "clear_day":
                n = sum(1 for a in self._all_acts if a.get("date") == ds)
                if not n:
                    return f"Nothing editable on {ds} to clear."
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds]
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Cleared {n} block(s) from {ds}."

            if name == "copy_day":
                src = resolve_date(args.get("from_date"), self._cur_date)
                dst = resolve_date(args.get("to_date"), self._cur_date)
                if src is None or dst is None:
                    return ("Error: couldn't understand the date(s). Use Month/Day "
                            "like 6/14, or 'tomorrow'.")
                if src == dst:
                    return "Error: source and target dates are the same."
                source = [a for a in self._all_acts if a.get("date") == src]
                if not source:
                    return f"Nothing editable on {src} to copy."
                merge = bool(args.get("merge"))
                copies = [{
                    "id": new_id(), "date": dst,
                    "startMin": a["startMin"], "endMin": a["endMin"],
                    "type": a["type"], "color": a["color"], "title": a["title"],
                } for a in source]
                # Either way, push the copies off the target day's read-only calendar
                # events so they never land on a meeting (merge also keeps existing blocks).
                if merge:
                    kept = [a for a in self._all_acts if a.get("date") == dst]
                    laid, n_adj, n_drop = sequentialize(kept + copies, blocked=self._cal_intervals(dst))
                    adj_note = "shifted to avoid overlaps"   # could be a meeting OR a kept block
                else:
                    laid, n_adj, n_drop = sequentialize(copies, blocked=self._cal_intervals(dst))
                    adj_note = "shifted to clear a meeting"  # copies-only, so only a meeting shifts them
                self._all_acts = [a for a in self._all_acts if a.get("date") != dst] + laid
                note = (f" ({n_adj} {adj_note}.)" if n_adj else "")
                if n_drop:
                    note += f" ({n_drop} didn't fit the day and were dropped.)"
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Copied {len(copies)} block(s) from {src} to {dst}.{note}"

            if name == "shift_blocks":
                mins = 0
                try:
                    if args.get("minutes") not in (None, ""):
                        mins += int(float(args["minutes"]))
                    if args.get("hours") not in (None, ""):
                        mins += 60 * int(float(args["hours"]))
                except (TypeError, ValueError):
                    return "Error: 'minutes' must be a number (positive = later, negative = earlier)."
                if not mins:
                    return "Error: give 'minutes' — positive = later, negative = earlier (120 = 2h later)."
                acts = [a for a in self._all_acts if a.get("date") == ds]
                if not acts:
                    return f"No editable blocks on {ds} to shift."
                for a in acts:
                    dur = a["endMin"] - a["startMin"]
                    ns  = max(core.DAY_START, min(a["startMin"] + mins, core.DAY_END - dur))
                    a["startMin"], a["endMin"] = ns, ns + dur
                # clamping at the day edges can pile blocks up — de-overlap the result,
                # and keep blocks off any calendar meetings
                fixed, n_adj, n_drop = sequentialize(acts, blocked=self._cal_intervals(ds))
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + fixed
                save_all_activities(self._all_acts)
                self._refresh_view()
                direction = "later" if mins > 0 else "earlier"
                out = f"Shifted {len(fixed)} block(s) on {ds} {abs(mins)} minutes {direction}."
                if n_adj:
                    out += f" ({n_adj} adjusted at the day edges.)"
                if n_drop:
                    out += f" ({n_drop} dropped — no longer fit in the day.)"
                return out

            if name == "replace_day":
                raw = args.get("blocks")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        return "Error: 'blocks' must be a list of {start, end, title, type}."
                if not isinstance(raw, list) or not raw:
                    return "Error: 'blocks' must be a non-empty list of {start, end, title, type}."
                new_acts, skipped = [], 0
                for b in raw:
                    try:
                        sm = parse_hhmm(str(b["start"]))
                        em = coerce_end_min(sm, parse_hhmm(str(b["end"])))
                        if em <= sm:
                            raise ValueError("end before start")
                        tid = str(b.get("type", "study"))
                        at  = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                        new_acts.append({
                            "id": new_id(), "date": ds, "startMin": sm, "endMin": em,
                            "type": at["id"], "color": at["color"],
                            "title": str(b.get("title") or at["label"]),
                        })
                    except Exception:
                        skipped += 1
                if not new_acts:
                    return "Error: none of the blocks were valid (need start, end as 24h HH:MM, title)."
                new_acts, n_adj, n_drop = sequentialize(new_acts, blocked=self._cal_intervals(ds))
                if not new_acts:
                    return "Error: the blocks don't fit within the day (00:00–24:00)."
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + new_acts
                save_all_activities(self._all_acts)
                self._refresh_view()
                lines = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}"
                                  for a in new_acts)
                out = f"Replaced {ds} with {len(new_acts)} blocks: {lines}."
                if n_adj:
                    out += f" ({n_adj} shifted to remove overlaps.)"
                if n_drop:
                    out += f" ({n_drop} dropped — didn't fit before 24:00.)"
                if skipped:
                    out += f" ({skipped} invalid block(s) skipped.)"
                return out

            if name == "add_recurring":
                sm = parse_hhmm(str(args["start"]))
                em = coerce_end_min(sm, parse_hhmm(str(args["end"])))
                if em <= sm:
                    return "Error: end must be after start (use 24:00 for end of day)."
                tid = str(args.get("type", "study"))
                at_t = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                title = str(args.get("title") or at_t["label"])
                targets = []
                if args.get("dates"):
                    for d in args["dates"]:
                        rd = resolve_date(d, self._cur_date)
                        if rd:
                            targets.append(rd)
                elif args.get("weekdays"):
                    wanted = set()
                    for w in args["weekdays"]:
                        wl = str(w).strip().lower()
                        if wl in ("weekday", "weekdays"):
                            wanted |= {0, 1, 2, 3, 4}
                        elif wl in ("weekend", "weekends"):
                            wanted |= {5, 6}
                        elif wl in ("daily", "everyday", "every day", "all"):
                            wanted |= set(range(7))
                        elif wl in _WEEKDAYS:
                            wanted.add(_WEEKDAYS[wl])
                    if not wanted:
                        return "Error: couldn't read 'weekdays'."
                    try:
                        weeks = max(1, min(8, int(args.get("weeks", 1))))
                    except (TypeError, ValueError):
                        weeks = 1
                    for i in range(7 * weeks):
                        d = self._cur_date + timedelta(days=i)
                        if d.weekday() in wanted:
                            targets.append(d.isoformat())
                else:
                    return "Error: give 'weekdays' (e.g. ['monday']) or a 'dates' list."
                targets = sorted(set(targets))[:60]
                if not targets:
                    return "Error: no matching dates."
                conflicts = []
                for tds in targets:
                    if any(b["startMin"] < em and b["endMin"] > sm
                           for b in self._all_acts if b.get("date") == tds):
                        conflicts.append(tds)
                    self._all_acts.append({
                        "id": new_id(), "date": tds, "startMin": sm, "endMin": em,
                        "type": at_t["id"], "color": at_t["color"], "title": title,
                    })
                save_all_activities(self._all_acts)
                self._refresh_view()
                out = (f"Added '{title}' {fmt_time(sm)}–{fmt_time(em)} on {len(targets)} "
                       f"day(s): {', '.join(targets)}.")
                if conflicts:
                    out += f" Note: overlaps existing blocks on {', '.join(conflicts)}."
                return out

            if name == "clear_range":
                rs = parse_hhmm(str(args["start"]))
                re_ = parse_hhmm(str(args["end"]))
                if re_ <= rs:
                    return "Error: end must be after start."
                hits = [a for a in self._all_acts if a.get("date") == ds
                        and a["startMin"] < re_ and a["endMin"] > rs]
                if not hits:
                    return f"Nothing editable between {fmt_time(rs)}–{fmt_time(re_)} on {ds}."
                for a in hits:
                    self._all_acts.remove(a)
                save_all_activities(self._all_acts)
                self._refresh_view()
                return (f"Cleared {len(hits)} block(s) in {fmt_time(rs)}–{fmt_time(re_)} on "
                        f"{ds}: " + ", ".join(f"'{a['title']}'" for a in hits))

            if name == "find_free_time":
                after  = parse_hhmm(str(args["after"]))  if args.get("after")  else core.DAY_START
                before = parse_hhmm(str(args["before"])) if args.get("before") else core.DAY_END
                dur = 0
                if args.get("duration") not in (None, ""):
                    try:
                        dur = int(float(args["duration"]))
                    except (TypeError, ValueError):
                        return "Error: 'duration' must be a number of minutes."
                gaps = self._free_gaps(ds, after, before)
                if dur:
                    gaps = [(s, e) for s, e in gaps if e - s >= dur]
                if not gaps:
                    return (f"No free {('≥ ' + fmt_dur(dur) + ' ') if dur else ''}slots on "
                            f"{ds}{(' between ' + fmt_time(after) + '–' + fmt_time(before)) if (args.get('after') or args.get('before')) else ''}.")
                return (f"Free time on {ds}: " +
                        ", ".join(f"{fmt_time(s)}–{fmt_time(e)} ({fmt_dur(e - s)})"
                                  for s, e in gaps))

            if name == "split_block":
                hits = self._select_acts(ds, args.get("title"), args.get("at"))
                if not hits:
                    avail = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}"
                                      for a in sorted(self._day_acts(),
                                                      key=lambda x: x["startMin"])) or "none"
                    return f"No block matching that on {ds}. Blocks: {avail}."
                if len(hits) > 1:
                    listing = "; ".join(f"'{a['title']}' at {fmt_time(a['startMin'])}"
                                        for a in sorted(hits, key=lambda x: x["startMin"])[:5])
                    return f"Ambiguous — {len(hits)} match: {listing}. Add 'at' to pick one."
                a = hits[0]
                try:
                    chunk = max(5, int(args.get("chunk", 30)))
                except (TypeError, ValueError):
                    chunk = 30
                try:
                    brk = max(0, int(args.get("break", 5)))
                except (TypeError, ValueError):
                    brk = 5
                # Breaks are downtime, not a continuation of the work block — give them their
                # own category (default free = rest) instead of inheriting the type.
                btid = str(args.get("break_type") or "free")
                b_at = next((t for t in ACTIVITY_TYPES if t["id"] == btid), None)
                if b_at is None:
                    b_at = next((t for t in ACTIVITY_TYPES if t["id"] == "free"), ACTIVITY_TYPES[0])
                s0, e0 = a["startMin"], a["endMin"]
                segs, cur = [], s0
                while cur < e0:
                    cend = min(cur + chunk, e0)
                    segs.append(("chunk", cur, cend)); cur = cend
                    if cur < e0 and brk > 0:
                        bend = min(cur + brk, e0)
                        segs.append(("break", cur, bend)); cur = bend
                while segs and segs[-1][0] == "break":   # no trailing break
                    segs.pop()
                n_chunks = sum(1 for k, _, _ in segs if k == "chunk")
                if n_chunks < 2:
                    return (f"'{a['title']}' ({fmt_dur(e0 - s0)}) is too short to split into "
                            f"{chunk}-min chunks.")
                self._all_acts.remove(a)
                ci = 0
                for kind, ss, ee in segs:
                    if kind == "chunk":
                        ci += 1
                        self._all_acts.append({
                            "id": new_id(), "date": ds, "startMin": ss, "endMin": ee,
                            "type": a["type"], "color": a["color"],
                            "title": f"{a['title']} ({ci})"})
                    else:   # breaks are downtime — their own category, not the work block's
                        self._all_acts.append({
                            "id": new_id(), "date": ds, "startMin": ss, "endMin": ee,
                            "type": b_at["id"], "color": b_at["color"], "title": "Break"})
                save_all_activities(self._all_acts)
                self._refresh_view()
                return (f"Split '{a['title']}' into {n_chunks} × {chunk}-min chunks"
                        f"{f' with {brk}-min breaks' if brk else ''}.")

            if name == "schedule_tasks":
                raw = args.get("tasks")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        return "Error: 'tasks' must be a list of {title, minutes, ...}."
                if not isinstance(raw, list) or not raw:
                    return "Error: give a non-empty 'tasks' list."
                ws = (parse_hhmm(str(args["day_start"])) if args.get("day_start")
                      else parse_hhmm(self._settings.get("plan_day_start", "08:00")))
                we = (parse_hhmm(str(args["day_end"]))   if args.get("day_end")
                      else parse_hhmm(self._settings.get("plan_day_end", "22:00")))
                # Planning today with no explicit start → don't place tasks in the past.
                if ds == date.today().isoformat() and not args.get("day_start"):
                    ws = max(ws, datetime.now().hour * 60 + datetime.now().minute)
                if we <= ws:
                    we = core.DAY_END
                windows = {"morning": (8*60, 12*60), "afternoon": (12*60, 17*60),
                           "evening": (17*60, 22*60), "night": (20*60, 24*60)}
                prio = {"high": 0, "urgent": 0, "important": 0, "normal": 1,
                        "medium": 1, "low": 2}
                tasks = []
                for i, t in enumerate(raw[:20]):
                    if not isinstance(t, dict):
                        continue
                    try:
                        mins = int(float(t.get("minutes") or t.get("duration") or 60))
                    except (TypeError, ValueError):
                        mins = 60
                    want = mins
                    mins = max(15, min(mins, we - ws))
                    tid = str(t.get("type", "study"))
                    at_t = next((x for x in ACTIVITY_TYPES if x["id"] == tid), ACTIVITY_TYPES[0])
                    tasks.append({
                        "title": str(t.get("title") or at_t["label"]), "mins": mins,
                        "type": at_t["id"], "color": at_t["color"],
                        "pr": prio.get(str(t.get("priority", "normal")).lower(), 1),
                        "prefer": str(t.get("prefer", "")).strip().lower(), "i": i,
                        "clamped": mins < want,
                    })
                if not tasks:
                    return "Error: no valid tasks."
                tasks.sort(key=lambda x: (x["pr"], x["i"]))
                occ = [(a["startMin"], a["endMin"]) for a in self._all_acts if a.get("date") == ds] + \
                      [(e["startMin"], e["endMin"])
                       for e in timed_cal_events(self._cal_by_date.get(ds, []))]
                # idempotent: don't re-add a task already on the day (repeat calls are safe)
                have = {norm_title(a["title"]) for a in self._all_acts if a.get("date") == ds}
                placed, unplaced, already, shortened = [], [], [], []
                for t in tasks:
                    if norm_title(t["title"]) in have:
                        already.append(t["title"]); continue
                    ranges = []
                    if t["prefer"] in windows:
                        pw = windows[t["prefer"]]
                        ranges.append((max(ws, pw[0]), min(we, pw[1])))
                    elif t["prefer"]:
                        try:
                            ps = parse_hhmm(t["prefer"]); ranges.append((max(ws, ps), we))
                        except ValueError:
                            pass
                    ranges.append((ws, we))   # fallback: whole waking window
                    slot = None
                    for a0, b0 in ranges:
                        if b0 - a0 < t["mins"]:
                            continue
                        for gs, ge in _free_slots(occ, a0, b0):
                            if ge - gs >= t["mins"]:
                                slot = (gs, gs + t["mins"]); break
                        if slot:
                            break
                    if slot:
                        occ.append(slot)
                        have.add(norm_title(t["title"]))
                        self._all_acts.append({
                            "id": new_id(), "date": ds, "startMin": slot[0], "endMin": slot[1],
                            "type": t["type"], "color": t["color"], "title": t["title"]})
                        placed.append((t["title"], slot))
                        if t.get("clamped"):
                            shortened.append(t["title"])
                    else:
                        unplaced.append(t["title"])
                if not placed:
                    if already and not unplaced:
                        return ("Those are already on {}'s schedule — nothing to add."
                                .format(ds))
                    return ("Couldn't fit any task in the free time on {} ({}–{}). Try a wider "
                            "window or shorter tasks.".format(ds, fmt_time(ws), fmt_time(we)))
                save_all_activities(self._all_acts)
                self._refresh_view()
                placed.sort(key=lambda x: x[1][0])
                out = "Scheduled on {}: ".format(ds) + ", ".join(
                    f"'{ti}' {fmt_time(s)}–{fmt_time(e)}" for ti, (s, e) in placed)
                if already:
                    out += " | Already there: " + ", ".join(already)
                if unplaced:
                    out += " | Couldn't fit (no free slot): " + ", ".join(unplaced)
                if shortened:
                    out += (f" | Shortened to fit the {fmt_time(ws)}–{fmt_time(we)} "
                            f"window: " + ", ".join(shortened))
                return out

            if name == "plan_day":
                raw_tasks = args.get("tasks")
                if isinstance(raw_tasks, str):
                    try: raw_tasks = json.loads(raw_tasks)
                    except Exception: return "Error: 'tasks' must be a list of {title, minutes, …}."
                if not isinstance(raw_tasks, list) or not raw_tasks:
                    return "Error: give a non-empty ordered 'tasks' list."
                raw_fixed = args.get("fixed") or []
                if isinstance(raw_fixed, str):
                    try: raw_fixed = json.loads(raw_fixed)
                    except Exception: raw_fixed = []

                def _atype(tid, default):
                    return next((t for t in ACTIVITY_TYPES if t["id"] == str(tid)),
                                next(t for t in ACTIVITY_TYPES if t["id"] == default))

                ws = (parse_hhmm(str(args["start"])) if args.get("start")
                      else parse_hhmm(self._settings.get("plan_day_start", "08:00")))
                if ds == date.today().isoformat() and not args.get("start"):
                    ws = max(ws, datetime.now().hour * 60 + datetime.now().minute)

                # Fixed anchors first; they (and calendar events) are obstacles tasks flow around.
                new_blocks, occ = [], list(self._cal_intervals(ds))
                for f in (raw_fixed if isinstance(raw_fixed, list) else []):
                    if not isinstance(f, dict) or not f.get("start"):
                        continue
                    try:
                        fs = parse_hhmm(str(f["start"]))
                    except ValueError:
                        continue
                    try:
                        fe = parse_hhmm(str(f["end"])) if f.get("end") else fs + max(5, int(f.get("minutes", 60)))
                    except (TypeError, ValueError):
                        fe = fs + 60
                    fe = min(fe, core.DAY_END)
                    if fe <= fs:
                        continue
                    at_f = _atype(f.get("type", "extra"), "extra")
                    new_blocks.append({"id": new_id(), "date": ds, "startMin": fs, "endMin": fe,
                                       "type": at_f["id"], "color": at_f["color"],
                                       "title": str(f.get("title") or at_f["label"])})
                    occ.append((fs, fe))

                # Ordered tasks: each gets its full focus time, split into chunks with breaks,
                # flowing past anchors/meetings. Breaks do NOT count toward a task's minutes.
                brk_t = _atype("free", "free")
                cursor, unplaced = ws, []
                for t in raw_tasks[:12]:
                    if not isinstance(t, dict):
                        continue
                    try: total = max(5, int(float(t.get("minutes") or 60)))
                    except (TypeError, ValueError): total = 60
                    at_t = _atype(t.get("type", "study"), "study")
                    try: chunk = max(5, int(t["chunk"])) if t.get("chunk") else total
                    except (TypeError, ValueError): chunk = total
                    chunk = min(chunk, total)
                    try: brk = max(0, int(t.get("break", 15 if chunk < total else 0)))
                    except (TypeError, ValueError): brk = 15 if chunk < total else 0
                    n_chunks = -(-total // chunk)
                    left = total
                    idx = 0
                    while left > 0:
                        clen = min(chunk, left)
                        slot = _earliest_fit(occ, cursor, clen)
                        if slot is None:
                            unplaced.append(str(t.get("title") or at_t["label"])); break
                        idx += 1
                        ttl = (f"{t.get('title') or at_t['label']} ({idx})"
                               if n_chunks > 1 else str(t.get("title") or at_t["label"]))
                        new_blocks.append({"id": new_id(), "date": ds, "startMin": slot,
                                           "endMin": slot + clen, "type": at_t["id"],
                                           "color": at_t["color"], "title": ttl})
                        occ.append((slot, slot + clen)); cursor = slot + clen; left -= clen
                        if left > 0 and brk > 0:
                            bslot = _earliest_fit(occ, cursor, brk)
                            if bslot == cursor:   # only a contiguous break (skip if an anchor butts up)
                                new_blocks.append({"id": new_id(), "date": ds, "startMin": bslot,
                                                   "endMin": bslot + brk, "type": brk_t["id"],
                                                   "color": brk_t["color"], "title": "Break"})
                                occ.append((bslot, bslot + brk)); cursor = bslot + brk

                if not new_blocks:
                    return "Error: couldn't place anything — check the start time and task minutes."
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + new_blocks
                save_all_activities(self._all_acts)
                self._refresh_view()
                lines = ", ".join(f"'{b['title']}' {fmt_time(b['startMin'])}–{fmt_time(b['endMin'])}"
                                  for b in sorted(new_blocks, key=lambda x: x["startMin"]))
                out = f"Planned {ds}: {lines}."
                if unplaced:
                    out += " | Couldn't fully fit: " + ", ".join(dict.fromkeys(unplaced))
                return out

            if name == "make_room":
                t = args.get("title")
                if not (t and str(t).strip()):
                    return "Error: give the appointment a 'title'."
                try:
                    es = parse_hhmm(str(args["start"])); ee = parse_hhmm(str(args["end"]))
                except (KeyError, ValueError):
                    return "Error: give the appointment 'start' and 'end' as 24h HH:MM."
                if ee <= es:
                    return "Error: the appointment's end must be after its start."
                try: bb = max(0, int(args.get("buffer_before", 0) or 0))
                except (TypeError, ValueError): bb = 0
                try: ba = max(0, int(args.get("buffer_after", 0) or 0))
                except (TypeError, ValueError): ba = 0
                tid  = str(args.get("type", "extra"))
                at_e = next((x for x in ACTIVITY_TYPES if x["id"] == tid),
                            next(x for x in ACTIVITY_TYPES if x["id"] == "extra"))
                brk_t = next((x for x in ACTIVITY_TYPES if x["id"] == "free"), ACTIVITY_TYPES[0])
                # Resolve any pinned blocks (kept exactly where they are).
                pin_args = args.get("pin") or []
                if isinstance(pin_args, str):
                    pin_args = [pin_args]
                pinned, pinned_ids = [], set()
                for p in (pin_args if isinstance(pin_args, list) else []):
                    ptitle = p.get("title") if isinstance(p, dict) else p
                    pat    = p.get("at") if isinstance(p, dict) else None
                    pn = norm_title(ptitle) if ptitle else None
                    try:
                        atm = parse_hhmm(str(pat)) if pat else None
                    except ValueError:
                        atm = None
                    for a in self._all_acts:
                        if a.get("date") != ds or a["id"] in pinned_ids:
                            continue
                        # EXACT title match for pinning, so e.g. pinning 'Workout/Break'
                        # doesn't also catch the plain 'Break' blocks (fuzzy would).
                        if pn is not None and norm_title(a.get("title", "")) != pn:
                            continue
                        if atm is not None and a["startMin"] != atm:
                            continue
                        pinned.append(a); pinned_ids.add(a["id"])
                # Fixed set = appointment (+ buffer Breaks) + pinned + calendar; everything else
                # keeps its order/duration and is shifted to flow around it.
                appt = {"id": new_id(), "date": ds, "startMin": es, "endMin": ee,
                        "type": at_e["id"], "color": at_e["color"], "title": str(t).strip()}
                new_fixed, win_s, win_e = [appt], es, ee
                if bb > 0:
                    win_s = max(core.DAY_START, es - bb)
                    new_fixed.append({"id": new_id(), "date": ds, "startMin": win_s, "endMin": es,
                                      "type": brk_t["id"], "color": brk_t["color"], "title": "Break"})
                if ba > 0:
                    win_e = min(core.DAY_END, ee + ba)
                    new_fixed.append({"id": new_id(), "date": ds, "startMin": ee, "endMin": win_e,
                                      "type": brk_t["id"], "color": brk_t["color"], "title": "Break"})
                # Reflow: blocks entirely before the appointment stay put; one straddling its
                # start is shrunk to end there; everything from the appointment onward ripples
                # after it, flowing past pinned blocks + meetings. Overflow shrinks the tail
                # rather than dropping it, so nothing is lost.
                all_obs = [(win_s, win_e)] + self._cal_intervals(ds) + \
                          [(p["startMin"], p["endMin"]) for p in pinned]
                movers = sorted([a for a in self._all_acts
                                 if a.get("date") == ds and a["id"] not in pinned_ids],
                                key=lambda x: x["startMin"])
                kept_movers, after, n_shrunk, n_drop = [], [], 0, 0
                for b in movers:
                    if b["endMin"] <= win_s:
                        kept_movers.append(b)                                  # before — unchanged
                    elif b["startMin"] < win_s and win_s - b["startMin"] >= 5:
                        kept_movers.append({**b, "endMin": win_s}); n_shrunk += 1  # straddler — trim
                    else:
                        after.append(b)                                        # ripple after the appt
                cursor = win_e
                for b in after:
                    dur = b["endMin"] - b["startMin"]
                    slot = _earliest_fit(all_obs, cursor, dur)
                    if slot is not None:
                        kept_movers.append({**b, "startMin": slot, "endMin": slot + dur})
                        cursor = slot + dur
                        continue
                    gap = next(((gs, ge) for gs, ge in _free_slots(all_obs, cursor, core.DAY_END)
                                if ge - gs >= 5), None)
                    if gap:
                        gs, ge = gap
                        clen = min(dur, ge - gs)
                        kept_movers.append({**b, "startMin": gs, "endMin": gs + clen}); n_shrunk += 1
                        cursor = gs + clen
                    else:
                        n_drop += 1
                kept = new_fixed + pinned + kept_movers
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + kept
                save_all_activities(self._all_acts)
                self._refresh_view()
                lines = ", ".join(f"'{b['title']}' {fmt_time(b['startMin'])}–{fmt_time(b['endMin'])}"
                                  for b in sorted(kept, key=lambda x: x["startMin"]))
                out = (f"Added '{appt['title']}' {fmt_time(es)}–{fmt_time(ee)} on {ds} and shifted "
                       f"the rest around it: {lines}.")
                if pinned:
                    out += " Kept fixed: " + ", ".join(dict.fromkeys(p["title"] for p in pinned)) + "."
                if n_shrunk:
                    out += f" ({n_shrunk} block(s) shrunk to fit.)"
                if n_drop:
                    out += f" ({n_drop} couldn't fit even shrunk — remove or shorten something.)"
                return out

            if name == "list_blocks":
                cal_all = self._cal_by_date.get(ds, [])
                cal_ad  = allday_cal_events(cal_all)
                cal     = sorted(timed_cal_events(cal_all), key=lambda x: x["startMin"])
                day_acts = sorted([x for x in self._all_acts if x.get("date") == ds],
                                  key=lambda x: x["startMin"])
                lines = [f"[calendar all-day] {ev['title']}" for ev in cal_ad]
                lines += [f"[calendar] {ev['title']}: {fmt_time(ev['startMin'])}–{fmt_time(ev['endMin'])}"
                          for ev in cal]
                lines += [f"[{a['type']}] {a['title']}: {fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}"
                          for a in day_acts]
                if not lines:
                    return f"Nothing scheduled on {ds}."
                # Conflict scan (timed cal only — all-day never occupies minutes).
                conflicts = []
                for i, a in enumerate(day_acts):
                    for ev in cal:
                        if a["startMin"] < ev["endMin"] and a["endMin"] > ev["startMin"]:
                            conflicts.append(
                                f"'{a['title']}' ({fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}) "
                                f"overlaps calendar event '{ev['title']}' "
                                f"({fmt_time(ev['startMin'])}–{fmt_time(ev['endMin'])})")
                    for b in day_acts[i + 1:]:
                        if a["startMin"] < b["endMin"] and a["endMin"] > b["startMin"]:
                            conflicts.append(
                                f"'{a['title']}' and '{b['title']}' overlap "
                                f"near {fmt_time(max(a['startMin'], b['startMin']))}")
                out = f"Schedule for {ds}:\n" + "\n".join(lines)
                if conflicts:
                    out += ("\nCONFLICTS — fix these, then re-check:\n"
                            + "\n".join(f"  - {c}" for c in conflicts))
                else:
                    out += "\nNo conflicts: nothing overlaps and no block sits on a meeting."
                return out

            if name == "reflow_from_now":
                try:
                    delay = int(float(args.get("minutes")))
                except (TypeError, ValueError):
                    return ("Error: 'minutes' must be a number (how far to push upcoming "
                            "blocks; positive = later, negative = earlier).")
                if delay == 0:
                    return "Error: give a non-zero 'minutes' (positive = later, negative = earlier)."
                if args.get("from"):
                    try:
                        cutoff = parse_hhmm(str(args["from"]))
                    except ValueError:
                        return "Error: couldn't read 'from' — use 24h HH:MM."
                elif ds == date.today().isoformat():
                    cutoff = datetime.now().hour * 60 + datetime.now().minute
                else:
                    cutoff = core.DAY_START
                movers = [a for a in self._all_acts
                          if a.get("date") == ds and a["startMin"] >= cutoff]
                if not movers:
                    return f"No blocks starting at or after {fmt_time(cutoff)} on {ds} to reflow."
                for a in movers:
                    dur = a["endMin"] - a["startMin"]
                    ns  = max(core.DAY_START, min(a["startMin"] + delay, core.DAY_END - dur))
                    a["startMin"], a["endMin"] = ns, ns + dur
                day = [a for a in self._all_acts if a.get("date") == ds]
                fixed, n_adj, n_drop = sequentialize(day, blocked=self._cal_intervals(ds))
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + fixed
                save_all_activities(self._all_acts)
                self._refresh_view()
                direction = "later" if delay > 0 else "earlier"
                out = (f"Reflowed {len(movers)} upcoming block(s) on {ds} {abs(delay)} min "
                       f"{direction} (from {fmt_time(cutoff)}).")
                if n_drop:
                    out += f" ({n_drop} no longer fit and were dropped.)"
                return out

            if name == "plan_for_deadline":
                title = str(args.get("title") or "").strip()
                if not title:
                    return "Error: give a 'title' for the work."
                dd = resolve_date(args.get("deadline"), self._cur_date)
                if dd is None:
                    return ("Error: couldn't understand 'deadline' — use a date like 6/20 "
                            "or a weekday like 'friday'.")
                try:
                    total = int(float(args.get("minutes") or args.get("total_minutes") or 0))
                except (TypeError, ValueError):
                    total = 0
                if total <= 0:
                    return "Error: give 'minutes' = the total time the whole job needs."
                try:
                    sess = max(15, int(float(args.get("session", 60))))
                except (TypeError, ValueError):
                    sess = 60
                tid  = str(args.get("type", "study"))
                at_t = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                start_iso = resolve_date(args.get("start_date"), self._cur_date) or date.today().isoformat()
                start    = max(date.fromisoformat(start_iso), date.today())
                deadline = date.fromisoformat(dd)
                days, d = [], start
                while d < deadline:               # days strictly before the deadline
                    days.append(d); d += timedelta(days=1)
                if not days and deadline >= date.today():
                    days = [deadline]             # deadline is today → use the day itself
                if not days:
                    return f"Error: the deadline {dd} has already passed."
                full, rem = divmod(total, sess)   # split total into daily sessions
                sizes = [sess] * full
                if rem >= 15:
                    sizes.append(rem)
                elif rem and sizes:
                    sizes[-1] += rem
                if not sizes:
                    sizes = [total]
                ws = parse_hhmm(self._settings.get("plan_day_start", "08:00"))
                we = parse_hhmm(self._settings.get("plan_day_end", "22:00"))
                placed, skipped, already, di = [], [], [], 0
                for k, length in enumerate(sizes, 1):
                    stitle, done = f"{title} ({k}/{len(sizes)})", False
                    for _ in range(len(days)):
                        day_d = days[di % len(days)]; di += 1
                        dstr  = day_d.isoformat()
                        have  = {norm_title(a["title"]) for a in self._all_acts if a.get("date") == dstr}
                        if norm_title(stitle) in have:
                            already.append(stitle); done = True; break
                        lo = ws
                        if dstr == date.today().isoformat():
                            lo = max(ws, datetime.now().hour * 60 + datetime.now().minute)
                        occ = [(a["startMin"], a["endMin"]) for a in self._all_acts if a.get("date") == dstr] + \
                              [(e["startMin"], e["endMin"])
                               for e in timed_cal_events(self._cal_by_date.get(dstr, []))]
                        slot = None
                        for gs, ge in _free_slots(occ, lo, we):
                            if ge - gs >= length:
                                slot = (gs, gs + length); break
                        if slot:
                            self._all_acts.append({
                                "id": new_id(), "date": dstr, "startMin": slot[0], "endMin": slot[1],
                                "type": at_t["id"], "color": at_t["color"], "title": stitle})
                            placed.append((dstr, slot)); done = True; break
                    if not done:
                        skipped.append(stitle)
                if not placed and already:
                    return f"All {len(already)} session(s) for '{title}' are already planned before {dd}."
                if not placed:
                    return (f"Couldn't fit any session for '{title}' before {dd} within "
                            f"{fmt_time(ws)}–{fmt_time(we)}. Try shorter sessions or a wider window.")
                save_all_activities(self._all_acts)
                self._refresh_view()
                placed.sort(key=lambda x: (x[0], x[1][0]))
                out = (f"Planned '{title}' for {dd}: {len(placed)} session(s) — " +
                       ", ".join(f"{dstr} {fmt_time(s)}–{fmt_time(e)}" for dstr, (s, e) in placed))
                if already:
                    out += f" | {len(already)} already there"
                if skipped:
                    out += f" | couldn't fit {len(skipped)} (no free slot before the deadline)"
                return out

            if name == "week_summary":
                if args.get("start") or args.get("end"):
                    s = resolve_date(args.get("start"), self._cur_date) or self._cur_date.isoformat()
                    e = resolve_date(args.get("end"), self._cur_date) or s
                else:
                    monday = self._cur_date - timedelta(days=self._cur_date.weekday())
                    s = monday.isoformat()
                    e = (monday + timedelta(days=6)).isoformat()
                if e < s:
                    s, e = e, s
                ndays = (date.fromisoformat(e) - date.fromisoformat(s)).days + 1
                totals = {}
                for a in self._all_acts:
                    if s <= a.get("date", "") <= e:
                        totals[a["type"]] = totals.get(a["type"], 0) + (a["endMin"] - a["startMin"])
                for dstr, evs in self._cal_by_date.items():
                    if s <= dstr <= e:
                        for ev in evs:
                            totals["calendar"] = totals.get("calendar", 0) + (ev["endMin"] - ev["startMin"])
                if not totals:
                    return f"Nothing scheduled between {s} and {e}."
                labels = {t["id"]: t["label"] for t in ACTIVITY_TYPES}
                labels["calendar"] = "Calendar"
                parts = [f"{labels.get(k, k)} {fmt_dur(v)} (~{fmt_dur(v // ndays)}/day)"
                         for k, v in sorted(totals.items(), key=lambda x: -x[1])]
                return f"{s} → {e} ({ndays} days): " + "; ".join(parts)

            return f"Unknown tool '{name}'."
        except KeyError as ex:
            return f"Error: missing argument {ex}."
        except ValueError as ex:
            return f"Error: {ex}"
        except Exception as ex:
            return f"Error: {ex}"

    # ── Status ─────────────────────────────────────────────────────────────
    def _set_status(self, msg, error=False):
        self._status_lbl.setText(msg)
        color = theme.C_ERR_TXT.name() if error else theme.C_MUTED.name()
        self._status_lbl.setStyleSheet(
            f"color:{color}; font-size:11px; padding:3px 14px; background:transparent;")

    # ── Now / Next indicator ─────────────────────────────────────────────────
    def _nownext_text(self) -> str:
        """Current 'Now / Next' line for the REAL clock and TODAY's schedule (user
        blocks + any loaded calendar events), regardless of the day being viewed."""
        today = date.today()
        nm = datetime.now().hour * 60 + datetime.now().minute
        return now_next_summary(
            self._day_acts(today) + timed_cal_events(self._day_cal(today)), nm)

    def _update_nownext(self):
        s = self._nownext_text()
        self._nownext_lbl.setText(s)
        self._refresh_tray_tooltip(s)

    def _refresh_tray_tooltip(self, nownext=None):
        """Compose the tray tooltip: version + Now/Next + (update line if available)."""
        if self._tray is None:
            return
        if nownext is None:
            nownext = self._nownext_text()
        lines = [f"Daily Scheduler v{core.APP_VERSION}"]
        if nownext:
            lines.append(nownext)
        if self._update_tag:
            lines.append(f"Update to v{strip_v(self._update_tag)} available")
        self._tray.setToolTip("\n".join(lines))

    # ── Auto-update check ────────────────────────────────────────────────────
    def _check_for_update(self):
        """Kick off a background GitHub release check (opt-out via settings).
        No-op if one is already running; the thread fails silently on any error."""
        if not self._settings.get("update_check_on", True):
            return
        if self._update_thread is not None and self._update_thread.isRunning():
            return
        t = platform_utils.UpdateCheckThread()                       # unparented; ref held below
        t.update_available.connect(self._on_update_available)
        t.finished.connect(t.deleteLater)
        t.finished.connect(lambda: setattr(self, "_update_thread", None))
        self._update_thread = t
        t.start()

    def _on_update_available(self, tag, url):
        ver = strip_v(tag)
        self._update_tag, self._update_url = tag, url
        self._update_btn.setText(f"⬆  Update available: v{ver}")
        self._update_btn.setToolTip(
            f"Version {ver} is available — click to open the releases page")
        self._update_btn.show()
        # Reflect it in the tray too, if the tray has been built yet (it may still
        # be retrying at Windows login — _setup_tray re-applies this when it lands).
        self._refresh_tray_tooltip()
        if self._update_act is not None:
            self._update_act.setText(f"⬆  Update to v{ver}…")
            self._update_act.setVisible(True)

    def _open_releases_page(self):
        QDesktopServices.openUrl(QUrl(self._update_url or RELEASES_PAGE))

    # ── Tray icon & notifications ────────────────────────────────────────────
    def _make_app_icon(self) -> QIcon:
        pm = QPixmap(64, 64); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(theme.C_ACCENT); p.setPen(Qt.NoPen)
        p.drawRoundedRect(6, 6, 52, 52, 14, 14)
        p.setBrush(theme.C_ON_ACCENT)
        p.drawRoundedRect(16, 14, 32, 6, 2, 2)        # calendar top bar
        p.setFont(QFont("Segoe UI", 20, QFont.Bold)); p.setPen(theme.C_ON_ACCENT)
        p.drawText(QRect(0, 14, 64, 50), Qt.AlignCenter, "◈")
        p.end()
        return QIcon(pm)

    def _setup_tray(self, _attempt=0):
        # Robust against the Windows login race: right after sign-in the shell often
        # (a) reports the tray UNAVAILABLE for a while, or (b) reports it AVAILABLE but
        # silently drops the icon's first add. We retry (a) for ~60 s and re-assert (b)
        # via _reassert_tray a few seconds later. Idempotent: builds the icon at most
        # once, so retry/self-heal/explorer-restart callers never stack duplicate icons.
        if self._really_quit:
            return
        if self._tray is not None:
            if not self._tray.isVisible():
                self._tray.show()
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            if not (_attempt == 0 and self._tray_retry_pending):   # don't start a 2nd chain
                if _attempt < 12:
                    self._tray_retry_pending = True
                    QTimer.singleShot(5000, lambda: self._setup_tray(_attempt + 1))
                else:
                    self._tray_retry_pending = False               # chain exhausted
            return
        self._tray_retry_pending = False
        self._tray = QSystemTrayIcon(self._make_app_icon(), self)
        self._tray.setToolTip(f"Daily Scheduler v{core.APP_VERSION}")
        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{ background: {theme.C_SURFACE.name()}; color: {theme.C_TEXT.name()};
                     border: 1px solid {theme.C_BORDER2.name()}; padding: 4px; }}
            QMenu::item {{ padding: 6px 16px; border-radius: {theme.RAD}px; }}
            QMenu::item:selected {{ background: {theme.C_SURF2.name()}; }}
        """)
        # "Update available" — hidden until a newer release is found (top of menu).
        self._update_act = menu.addAction("")
        self._update_act.setVisible(False)
        self._update_act.triggered.connect(self._open_releases_page)
        open_act = menu.addAction("Open Daily Scheduler")
        open_act.triggered.connect(self._show_from_tray)
        self._notify_act = menu.addAction("Notify when blocks start")
        self._notify_act.setCheckable(True)
        self._notify_act.setChecked(self._notify_on)
        self._notify_act.toggled.connect(self._toggle_notify)
        self._dnd_act = menu.addAction("Override Do Not Disturb")
        self._dnd_act.setCheckable(True)
        self._dnd_act.setChecked(self._dnd_override)
        self._dnd_act.setToolTip("Show an always-on-top alert that breaks through "
                                 "Do Not Disturb / Focus Assist")
        self._dnd_act.toggled.connect(self._toggle_dnd)
        test_act = menu.addAction("Test notification")
        test_act.triggered.connect(self._test_notification)
        menu.addSeparator()
        settings_act = menu.addAction("Settings…")
        settings_act.triggered.connect(self._open_settings)
        self._startup_act = menu.addAction(
            "Start with Windows" if platform.system() == "Windows" else "Start at login")
        self._startup_act.setCheckable(True)
        self._startup_act.setChecked(is_startup_enabled())
        self._startup_act.toggled.connect(self._toggle_startup)
        menu.addSeparator()
        quit_act = menu.addAction("Quit")
        quit_act.triggered.connect(self._quit_app)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        # If an update was already found before the tray existed, surface it now.
        if self._update_tag:
            self._on_update_available(self._update_tag, self._update_url)
        self._refresh_tray_tooltip()   # seed tooltip with version + Now/Next
        # At login the shell may accept isSystemTrayAvailable() but drop this first
        # add. Re-assert once it has settled (only when auto-launched, to avoid a
        # cosmetic flicker on a normal foreground launch where the icon is fine).
        if "--startup" in sys.argv:
            QTimer.singleShot(4000, self._reassert_tray)
            QTimer.singleShot(12000, self._reassert_tray)

    def _reassert_tray(self):
        # Force Windows to re-add the icon. hide() issues NIM_DELETE before show()'s
        # NIM_ADD, so this re-registers a dropped icon WITHOUT leaving a duplicate.
        if self._tray is not None and not self._really_quit:
            self._tray.hide()
            self._tray.show()

    def _update_setting(self, key, value):
        self._settings[key] = value
        save_settings(self._settings)

    def _toggle_notify(self, v):
        self._notify_on = v
        self._update_setting("notify_on", v)

    def _toggle_dnd(self, v):
        self._dnd_override = v
        self._update_setting("dnd_override", v)

    def _open_settings(self):
        dlg = SettingsDialog(self._settings, self)
        if dlg.exec() != QDialog.Accepted:
            return
        old_theme = self._settings.get("theme")
        old_cals  = self._settings.get("calendar_ids", "primary")
        self._settings = dlg.values
        save_settings(self._settings)
        # Startup shortcut (a filesystem .lnk, so it persists on its own)
        if dlg.startup_requested != is_startup_enabled():
            set_startup(dlg.startup_requested)
        # Live-apply everything except the theme (which needs a rebuild)
        self._notify_on    = self._settings["notify_on"]
        self._dnd_override = self._settings["dnd_override"]
        for act, val in ((self._notify_act, self._notify_on),
                         (self._dnd_act, self._dnd_override),
                         (self._startup_act, is_startup_enabled())):
            if act:
                act.blockSignals(True); act.setChecked(val); act.blockSignals(False)
        self._ai_panel.apply_settings(self._settings)
        # Backup restore (staged in the dialog)
        if getattr(dlg, "restored_acts", None) is not None:
            self._manual_snapshot()
            self._all_acts = dlg.restored_acts
            save_all_activities(self._all_acts)
            self._ai_undo_invalidate()
            self._manual_undo.clear()   # history after restore is meaningless
            self._manual_redo.clear()
            self._refresh_view()
            self._set_status(f"Restored schedule ({len(self._all_acts)} blocks).")
        # Calendar ID change → re-fetch
        if self._settings.get("calendar_ids", "primary") != old_cals:
            self._cal_by_date.clear()
            self._fetched_keys.clear()
            self._ensure_cal_for_view()
            self._prefetch_ai_months()
        if self._settings.get("theme") != old_theme:
            QMessageBox.information(
                self, "Theme changed",
                "The new theme will be applied the next time you open Daily Scheduler.")

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self):
        # Self-heal the tray icon (e.g. if explorer restarted and evicted it) so a
        # surface ping from a second launch always restores both window and icon.
        self._setup_tray()
        self.showNormal(); self.raise_(); self.activateWindow()

    def _test_notification(self):
        self._alert("✓ Notifications are working",
                    "This is how you'll be alerted when a block starts."
                    + (" (Do Not Disturb override is ON.)" if self._dnd_override else ""),
                    kind="test")

    # ── Alerting ─────────────────────────────────────────────────────────────
    def _alert(self, title, body, *, kind: str = "start"):
        """Fire a block alert. With DND override on, draw our own always-on-top popup
        (+ sound) so it shows even under Do Not Disturb; otherwise a normal tray toast.
        If the tray icon is not ready yet, fall back to the in-app popup so the alert
        is never silently dropped. `kind` is start | end | test — drives badge/color."""
        if self._settings.get("notify_sound", True):
            self._play_alert_sound()
        if self._dnd_override or not self._tray:
            self._show_alert_popup(title, body, kind=kind, play_sound=False)
        else:
            self._tray.showMessage(title, body, self._make_app_icon(), 12000)

    def _play_alert_sound(self):
        if not self._settings.get("notify_sound", True):
            return
        tone = str(self._settings.get("notify_tone", "chime") or "chime")
        vol  = int(self._settings.get("notify_volume", 80) or 80) / 100.0
        play_alert_sound(self, tone=tone, volume=vol)

    def _show_alert_popup(self, title, body, *, kind: str = "start", play_sound: bool = True):
        if play_sound and self._settings.get("notify_sound", True):
            self._play_alert_sound()
        popup = AlertPopup(title, body, self._make_app_icon(), kind=kind)
        popup.destroyed.connect(lambda *_: self._popups.remove(popup)
                                if popup in self._popups else None)
        self._popups.append(popup)
        geo = QApplication.primaryScreen().availableGeometry()
        idx = max(0, len(self._popups) - 1)
        # Stack newer popups upward; taller card (~110px) than the old toast
        popup.show_at(geo.right() - popup.width() - 16,
                      geo.bottom() - 16 - idx * 118)

    def _toggle_startup(self, enabled):
        ok = set_startup(enabled)
        if not ok:
            # revert the checkbox to the true state without re-firing this handler
            self._startup_act.blockSignals(True)
            self._startup_act.setChecked(is_startup_enabled())
            self._startup_act.blockSignals(False)
            if self._tray:
                self._tray.showMessage("Couldn't update startup setting",
                    "The system blocked the change.", self._make_app_icon(), 5000)
            return
        if self._tray:
            msg = ("Daily Scheduler will open when you sign in "
                   "(the AI server stays off until you start it)."
                   if enabled else "Removed from startup.")
            self._tray.showMessage("Startup setting updated", msg,
                                   self._make_app_icon(), 5000)

    def _quit_app(self):
        self._really_quit = True
        self._persist_layout_splits_now()   # flush any debounced layout save
        if self._tray:
            self._tray.hide()
        QApplication.quit()

    NOTIFY_WINDOW = 2   # minutes — only notify a block starting right around now

    def _check_block_starts(self):
        """Notify only for blocks on TODAY that are starting (or ending) right now
        within a small window. A tight window — rather than 'anything since the last
        check' — means a forward clock jump can't replay a backlog of notifications."""
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        today = date.today().isoformat()
        if today != self._notified_day:       # new day → forget yesterday's notifications
            self._notified.clear()
            self._notified_ends.clear()
            self._notified_day = today
            purge_old_alert_marks(today)

        # Start alerts
        if self._notify_on:
            for b in self._all_acts:
                if b.get("date") != today:
                    continue
                sm = b["startMin"]
                key = (b["id"], sm)
                if key in self._notified:
                    continue
                lead = int(self._settings.get("notify_lead_min", 0) or 0)
                if start_alert_due(sm, now_min, lead=lead, window=self.NOTIFY_WINDOW):
                    self._notified.add(key)   # this process won't re-check this block
                    # Fire exactly once even if another instance is also running: only
                    # the process that wins the atomic claim shows the alert.
                    if claim_block_alert(today, b["id"], sm):
                        when = f"Starting in {lead} min · " if lead else "Starting now · "
                        self._alert(
                            f"▶ {b['title']}",
                            f"{when}{fmt_time(b['startMin'])} – {fmt_time(b['endMin'])}")

        # End-of-block chime — opt-in only (default off). Same cross-process claim as starts.
        # endMin=1440 (24:00) is handled by end_alert_due (wall clock max is 23:59).
        if self._settings.get("notify_end_chime", False):
            for b in self._all_acts:
                if b.get("date") != today:
                    continue
                em = b["endMin"]
                ekey = (b["id"], em)
                if ekey in self._notified_ends:
                    continue
                if end_alert_due(em, now_min, window=self.NOTIFY_WINDOW):
                    self._notified_ends.add(ekey)
                    if claim_block_alert(today, f"end_{b['id']}", em):
                        # Visual card when start-notify or DND popup is on; else sound only
                        if self._notify_on or self._dnd_override:
                            self._alert(
                                f"■ {b['title']} ended",
                                f"{fmt_time(b['startMin'])} – {fmt_time(b['endMin'])}",
                                kind="end")
                        else:
                            self._play_alert_sound()

    def closeEvent(self, ev):
        # Closing the window keeps the app alive in the tray so reminders still fire.
        # Without a tray (or on explicit Quit), really exit.
        self._persist_layout_splits_now()   # flush any debounced layout save
        if self._really_quit or not self._tray:
            ev.accept()
            QApplication.quit()
            return
        ev.ignore()
        self.hide()
        if not self._tray_hinted:
            self._tray_hinted = True
            self._tray.showMessage(
                "Daily Scheduler is still running",
                "It stays in the tray so it can remind you when blocks start. "
                "Right-click the tray icon to quit.",
                self._make_app_icon(), 6000)
