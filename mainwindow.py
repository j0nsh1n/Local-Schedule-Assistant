"""Daily Scheduler — the main application window.

MainWindow owns the application state (the schedule, the calendar cache, the
current date/view) and wires every widget together. The AI tool implementations
it exposes to the assistant live in ai_tools.AIToolsMixin, which it inherits.

Sections in this file, in order (each has a `# ──` banner):

    App page layout ...... _build_app / _build_header — the QStackedWidget,
                           the day/week/month/year stack, the splitters
    Boot ................. first-run setup screen vs. straight to the app
    Google Calendar ...... auth thread, range fetching, per-month caching
    Per-day accessors .... _day_acts / _day_cal / _cal_intervals — the last is
                           what keeps AI placement off read-only meetings
    Navigation ........... view switching and ‹ Today ›
    View refresh ......... _refresh_view — the single repaint path; call it
                           after ANY schedule mutation
    Activity actions ..... create/edit/drag/delete + Ctrl+Z / Ctrl+Y history
    Layout splitters ..... debounced persistence of pane sizes
    AI panel ............. panel wiring, per-turn context, snapshot/undo
    Status / Now-Next .... status bar text and the live "Now: … Next: …" line
    Auto-update check .... GitHub releases poll, notify-only
    Tray icon ............ creation, retry, re-assert (see the v2.5.3 lesson)
    Alerting ............. block-start alerts, DND-override popup, sound

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

import sys
import platform
import calendar as _cal
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QDialog, QStackedWidget, QMessageBox, QMenu, QSystemTrayIcon,
    QGraphicsOpacityEffect, QSplitter,
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox,
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
    AI_READONLY_TOOLS,
    AI_UNDO_KEEP,
    MANUAL_UNDO_KEEP,
    RELEASES_PAGE,
    allday_cal_events,
    claim_block_alert,
    end_alert_due,
    fmt_time,
    load_all_activities,
    load_settings,
    min_to_y,
    new_id,
    now_next_summary,
    parse_calendar_ids,
    purge_old_alert_marks,
    save_all_activities,
    save_settings,
    start_alert_due,
    strip_v,
    timed_cal_events,
    week_ahead_lines,
)
from theme import _rgba, _splitter_qss
from gcal import GoogleAuthThread
from platform_utils import (
    DesktopNotifyThread, is_startup_enabled, play_alert_sound, set_startup,
)
from views import MonthViewWidget, SidebarWidget, TimelineWidget, WeekViewWidget, YearViewWidget
from dialogs import AddActivityDialog, AlertPopup, SettingsDialog, SetupWidget
from aipanel import AIPanel
from ai_tools import AIToolsMixin



class MainWindow(AIToolsMixin, QMainWindow):
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
        self._notify_threads: List[QThread] = []   # in-flight DesktopNotifyThreads
        self._all_acts:    List[Dict] = load_all_activities()
        self._ai_undo:     List[List[Dict]] = []   # schedule snapshots for AI undo
        self._manual_undo: List[List[Dict]] = []   # v4.0: Ctrl+Z (manual edits + AI turns)
        self._manual_redo: List[List[Dict]] = []   # v4.1: Ctrl+Y restores what Ctrl+Z undid
        self._ai_turn_snapshotted = False
        self._ai_turn_active = False   # a turn is streaming — Undo is locked meanwhile
        self._selected_aid: Optional[str] = None   # last focused user block (copy/dup target)
        self._clip_act: Optional[Dict] = None      # clipboard: one block (fields sans id)
        self._clip_day: Optional[List[Dict]] = None  # clipboard: whole day of blocks
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
        # Copy / paste / duplicate / delete blocks (skip when a text field has focus)
        for seq, slot in (
            ("Ctrl+C", self._shortcut_copy),
            ("Ctrl+V", self._shortcut_paste),
            ("Ctrl+D", self._shortcut_duplicate),
            ("Delete", self._shortcut_delete),
            ("Backspace", self._shortcut_delete),
        ):
            sh = QShortcut(QKeySequence(seq), self)
            sh.setContext(Qt.WindowShortcut)
            sh.activated.connect(slot)

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
        self._timeline.activity_selected.connect(self._select_activity)
        self._timeline.activity_copy_req.connect(self._copy_activity)
        self._timeline.activity_dup_req.connect(self._duplicate_activity)
        self._timeline.activity_paste_req.connect(self._paste_activity)
        self._timeline.day_copy_req.connect(lambda: self._copy_day())
        self._timeline.day_dup_req.connect(lambda: self._duplicate_day())
        self._timeline.day_paste_req.connect(lambda: self._paste_day())
        self._timeline.day_clear_req.connect(lambda: self._clear_day())
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
        self._week_view.activity_selected.connect(self._select_activity)
        self._week_view.activity_copy_req.connect(self._copy_activity)
        self._week_view.activity_dup_req.connect(self._duplicate_activity)
        self._week_view.activity_delete_req.connect(self._delete_activity)
        self._week_view.activity_paste_req.connect(self._paste_activity)
        self._week_view.day_copy_req.connect(self._copy_day)
        self._week_view.day_dup_req.connect(self._duplicate_day)
        self._week_view.day_paste_req.connect(self._paste_day)
        self._week_view.day_clear_req.connect(self._clear_day)
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

    # ── Google Calendar (auth, range fetch, per-month cache) ───────────────
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
        # Restore selection ring after set_data / set_week repaints.
        if hasattr(self, "_timeline"):
            self._timeline.set_selected(self._selected_aid)
        if hasattr(self, "_week_view"):
            self._week_view.set_selected(self._selected_aid)

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
        self._selected_aid = aid
        dlg = AddActivityDialog(act["startMin"], act["endMin"], act["type"],
                                existing=act, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._manual_snapshot()
        if dlg.result_deleted:
            self._all_acts = [a for a in self._all_acts if a["id"] != aid]
            if self._selected_aid == aid:
                self._selected_aid = None
        elif dlg.result_activity:
            self._all_acts = [dlg.result_activity if a["id"] == aid else a
                              for a in self._all_acts]
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()

    def _commit_activity_change(self, aid, start, end):
        """Apply a drag move/resize to an existing block."""
        self._selected_aid = aid
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
        if self._selected_aid == aid:
            self._selected_aid = None
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()

    # ── Copy / paste / duplicate ────────────────────────────────────────────
    def _text_field_focused(self) -> bool:
        """True when the user is in a text control — leave Ctrl+C/V to Qt."""
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox))

    def _select_activity(self, aid: str):
        # Empty string from views means "deselect".
        self._selected_aid = aid or None
        # Keep Day/Week selection rings in sync after clicks.
        if hasattr(self, "_timeline"):
            self._timeline.set_selected(self._selected_aid)
        if hasattr(self, "_week_view"):
            self._week_view.set_selected(self._selected_aid)

    def _find_act(self, aid: Optional[str]) -> Optional[Dict]:
        if not aid:
            return None
        return next((a for a in self._all_acts if a.get("id") == aid), None)

    def _clip_from_act(self, act: Dict) -> Dict:
        """Clipboard payload: all fields except a unique id (regenerated on paste)."""
        return {k: v for k, v in act.items() if k != "id"}

    def _shortcut_copy(self):
        if self._text_field_focused():
            return
        self._copy_activity(self._selected_aid)

    def _shortcut_paste(self):
        if self._text_field_focused():
            return
        # Prefer day paste if the last copy was a whole day and no single block is selected.
        if self._clip_day and not self._selected_aid and not self._clip_act:
            self._paste_day()
        else:
            self._paste_activity()

    def _shortcut_duplicate(self):
        if self._text_field_focused():
            return
        self._duplicate_activity(self._selected_aid)

    def _shortcut_delete(self):
        if self._text_field_focused():
            return
        aid = self._selected_aid
        if not aid or not self._find_act(aid):
            self._set_status("Nothing to delete — click a block first.")
            return
        self._delete_activity(aid)
        self._set_status("Deleted.  (Ctrl+Z to undo)")

    def _copy_activity(self, aid: Optional[str] = None):
        act = self._find_act(aid or self._selected_aid)
        if not act:
            self._set_status("Nothing to copy — click a block first.")
            return
        self._selected_aid = act["id"]
        self._clip_act = self._clip_from_act(act)
        self._set_status(f"Copied “{act.get('title') or 'block'}”  (Ctrl+V to paste)")

    def _duplicate_activity(self, aid: Optional[str] = None):
        """Clone a block onto the same day/time (side-by-side via overlap layout)."""
        act = self._find_act(aid or self._selected_aid)
        if not act:
            self._set_status("Nothing to duplicate — click a block first.")
            return
        self._manual_snapshot()
        clone = self._clip_from_act(act)
        clone["id"] = new_id()
        # Keep date/times identical — week/day overlap columns show both.
        self._all_acts.append(clone)
        self._selected_aid = clone["id"]
        self._clip_act = self._clip_from_act(clone)  # so Ctrl+V works next too
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()
        self._set_status(f"Duplicated “{clone.get('title') or 'block'}”  (Ctrl+Z to undo)")

    def _paste_activity(self, on_date=None):
        """Paste the clipboard block onto `on_date` or the currently viewed day."""
        if not self._clip_act:
            self._set_status("Clipboard empty — copy a block first (Ctrl+C or right-click).")
            return
        if isinstance(on_date, date):
            target = on_date
        else:
            target = self._cur_date
        self._manual_snapshot()
        clone = dict(self._clip_act)
        clone["id"] = new_id()
        clone["date"] = target.isoformat()
        sm = int(clone.get("startMin") or 0)
        em = int(clone.get("endMin") or (sm + 60))
        sm = max(core.DAY_START, min(sm, core.DAY_END - 5))
        em = max(sm + 5, min(em, core.DAY_END))
        clone["startMin"] = sm
        clone["endMin"] = em
        self._all_acts.append(clone)
        self._selected_aid = clone["id"]
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()
        self._set_status(
            f"Pasted “{clone.get('title') or 'block'}” on "
            f"{target.strftime('%a %b %d')}  (Ctrl+Z to undo)")

    def _copy_day(self, d=None):
        """Copy every editable block on day `d` (default: viewed day)."""
        if not isinstance(d, date):
            d = self._cur_date
        ds = d.isoformat()
        acts = [a for a in self._all_acts if a.get("date") == ds]
        if not acts:
            self._set_status(f"No blocks on {d.strftime('%a %b %d')} to copy.")
            return
        self._clip_day = [self._clip_from_act(a) for a in acts]
        # Also seed single-block clipboard with the first so Ctrl+V still does something.
        self._clip_act = dict(self._clip_day[0]) if self._clip_day else None
        self._set_status(
            f"Copied day {d.strftime('%a %b %d')} ({len(acts)} block(s)) — "
            f"right-click another day → Paste day")

    def _duplicate_day(self, d=None):
        """Copy all blocks from `d` onto the following day (merge, no wipe)."""
        if not isinstance(d, date):
            d = self._cur_date
        src = d.isoformat()
        dst_d = d + timedelta(days=1)
        dst = dst_d.isoformat()
        acts = [a for a in self._all_acts if a.get("date") == src]
        if not acts:
            self._set_status(f"No blocks on {d.strftime('%a %b %d')} to duplicate.")
            return
        self._manual_snapshot()
        clones = []
        for a in acts:
            c = self._clip_from_act(a)
            c["id"] = new_id()
            c["date"] = dst
            clones.append(c)
        self._all_acts.extend(clones)
        self._clip_day = [self._clip_from_act(c) for c in clones]
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()
        self._set_status(
            f"Duplicated {len(clones)} block(s) → {dst_d.strftime('%a %b %d')}  "
            f"(Ctrl+Z to undo)")

    def _paste_day(self, d=None):
        """Paste the day clipboard onto `d` (merge; does not wipe existing)."""
        if not self._clip_day:
            self._set_status("No day on clipboard — right-click a day → Copy day first.")
            return
        if not isinstance(d, date):
            d = self._cur_date
        ds = d.isoformat()
        self._manual_snapshot()
        clones = []
        for tmpl in self._clip_day:
            c = dict(tmpl)
            c["id"] = new_id()
            c["date"] = ds
            sm = int(c.get("startMin") or 0)
            em = int(c.get("endMin") or (sm + 60))
            sm = max(core.DAY_START, min(sm, core.DAY_END - 5))
            em = max(sm + 5, min(em, core.DAY_END))
            c["startMin"] = sm
            c["endMin"] = em
            clones.append(c)
        self._all_acts.extend(clones)
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()
        self._set_status(
            f"Pasted {len(clones)} block(s) onto {d.strftime('%a %b %d')}  "
            f"(Ctrl+Z to undo)")

    def _clear_day(self, d=None):
        """Remove every editable block on day `d` (default: viewed day). Confirms first."""
        if not isinstance(d, date):
            d = self._cur_date
        ds = d.isoformat()
        acts = [a for a in self._all_acts if a.get("date") == ds]
        if not acts:
            self._set_status(f"{d.strftime('%a %b %d')} is already empty.")
            return
        confirm = QMessageBox.question(
            self, "Clear day",
            f"Delete all {len(acts)} editable block(s) on "
            f"{d.strftime('%A, %b %d')}?\n\n"
            f"Google Calendar events are not touched.\n"
            f"You can undo with Ctrl+Z.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        self._manual_snapshot()
        self._all_acts = [a for a in self._all_acts if a.get("date") != ds]
        if self._selected_aid and not any(
                a.get("id") == self._selected_aid for a in self._all_acts):
            self._selected_aid = None
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()
        self._set_status(
            f"Cleared {len(acts)} block(s) from {d.strftime('%a %b %d')}  "
            f"(Ctrl+Z to undo)")

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
        """Fire a block alert.

        Linux (esp. Wayland): prefer FreeDesktop Notifications — Plasma places
        them in the configured corner. Custom Qt toasts are centered by the
        compositor and cannot be fixed from the client.

        Windows: DND override uses our always-on-top AlertPopup; otherwise tray
        toast. Fallback to the popup if tray/DBus is unavailable.
        `kind` is start | end | test — drives badge/color on the custom card.

        The D-Bus call runs on a worker thread (see DesktopNotifyThread), so a
        wedged session bus can't freeze the window; the sound plays immediately
        either way, and the fallback only runs if the daemon actually refused."""
        if self._settings.get("notify_sound", True):
            self._play_alert_sound()
        # Linux: system notification daemon owns corner placement.
        if platform.system() == "Linux":
            # Critical urgency when "override DND" is on; normal otherwise.
            urg = 2 if self._dnd_override else 1
            prefix = {"end": "Ended · ", "test": "Test · "}.get(kind, "")
            t = DesktopNotifyThread(f"{prefix}{title}" if prefix else title,
                                    title, body, kind, timeout_ms=12000, urgency=urg)
            # Hold a ref until finished: an unreferenced QThread can be GC'd
            # mid-run and segfault (the v3.7.1 OllamaCheckThread crash).
            self._notify_threads.append(t)
            t.result.connect(self._on_notify_result)
            t.finished.connect(self._on_notify_finished)
            t.start()
            return
        self._alert_fallback(title, body, kind)

    def _on_notify_result(self, ok, title, body, kind):
        """Queued back onto the GUI thread by Qt (bound slot on a QObject), so
        it is safe to build widgets here."""
        if not ok:
            self._alert_fallback(title, body, kind)

    def _on_notify_finished(self):
        t = self.sender()
        if t in self._notify_threads:
            self._notify_threads.remove(t)
        if t is not None:
            t.deleteLater()

    def _alert_fallback(self, title, body, kind: str = "start"):
        """Tray toast normally; our own always-on-top card when DND override is
        on (a toast can be suppressed by DND) or when there is no tray yet."""
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

    def _alert_target_screen(self):
        """Screen for alert toasts: prefer the monitor with this window, else
        the one under the cursor, else primary. Dual-head setups often put
        'primary' on the right (x=2560); using the main window's screen keeps
        alerts on the display the user is looking at."""
        scr = None
        try:
            wh = self.windowHandle()
            if wh is not None:
                scr = wh.screen()
        except Exception:
            scr = None
        if scr is None:
            try:
                from PySide6.QtGui import QCursor
                scr = QApplication.screenAt(QCursor.pos())
            except Exception:
                scr = None
        if scr is None:
            scr = QApplication.primaryScreen()
        return scr

    def _show_alert_popup(self, title, body, *, kind: str = "start", play_sound: bool = True):
        if play_sound and self._settings.get("notify_sound", True):
            self._play_alert_sound()
        popup = AlertPopup(title, body, self._make_app_icon(), kind=kind)
        popup.destroyed.connect(lambda *_: self._popups.remove(popup)
                                if popup in self._popups else None)
        self._popups.append(popup)
        # Stack newer popups upward on the chosen screen's bottom-right corner.
        # (Wayland may still ignore free placement — show_corner re-applies after
        # map; KWin rule on title "Daily Scheduler Alert" is the fallback.)
        idx = max(0, len(self._popups) - 1)
        popup.show_corner(self._alert_target_screen(), stack_idx=idx)

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
