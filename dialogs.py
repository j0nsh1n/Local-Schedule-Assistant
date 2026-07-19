"""Daily Scheduler — dialogs and popups (add/edit, setup, alerts, settings).

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

import shutil
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional, List, Dict

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QScrollArea, QFrame,
    QDialog, QFileDialog, QTimeEdit, QSizePolicy,
    QMessageBox, QGridLayout, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox, QFormLayout,
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, QTime,
)
from PySide6.QtGui import (
    QIcon,
)

import core
import theme
import ai
from core import (
    ACTIVITY_TYPES,
    DEFAULT_MODEL,
    DEFAULT_THEME,
    coerce_end_min,
    list_schedule_backups,
    load_activities_from_path,
    new_id,
    parse_hhmm,
    today_str,
)
from theme import THEMES, _rgba, style_activity_type_chip
from ai import default_ollama_models_dir, model_is_installed, model_when_text, show_model_guide
from platform_utils import NOTIFY_TONES, is_startup_enabled, play_alert_sound


# ══════════════════════════════════════════════════════════════════════════
#  ADD ACTIVITY DIALOG
# ══════════════════════════════════════════════════════════════════════════
class AddActivityDialog(QDialog):
    def __init__(self, start_min, end_min, sel_type, for_date=None,
                 existing=None, parent=None):
        super().__init__(parent)
        self._existing = existing
        is_edit = existing is not None
        if is_edit:
            sel_type  = existing.get("type", sel_type)
            start_min = existing["startMin"]
            end_min   = existing["endMin"]
            for_date  = existing.get("date", for_date)
        self.setWindowTitle("Edit Activity" if is_edit else "Add Activity")
        # Wide enough for a 2-col type grid with full labels; height scrolls.
        self.setMinimumWidth(400)
        self.setFixedWidth(420)
        self.result_activity = None
        self.result_deleted  = False
        self._sel = sel_type
        self._for_date = for_date or today_str()

        self.setStyleSheet(f"""
            QDialog   {{ background: {theme.C_SURFACE.name()}; color: {theme.C_TEXT.name()}; }}
            QLabel    {{ background: transparent; color: {theme.C_TEXT.name()}; }}
            QTimeEdit, QLineEdit {{
                background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
                color: {theme.C_TEXT.name()}; padding: 7px 10px; border-radius: {theme.RAD}px;
            }}
            QTimeEdit:focus, QLineEdit:focus {{ border-color: {theme.C_ACCENT.name()}; }}
            QScrollArea {{ background: transparent; border: none; }}
        """)

        lay = QVBoxLayout(self)
        lay.setSpacing(14); lay.setContentsMargins(20, 18, 20, 18)

        title = QLabel("Edit Activity" if is_edit else "Log Activity")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        lay.addWidget(title)

        # Type buttons — 2 columns so long labels (Extracurriculars, Class / School)
        # stay fully inside the dialog. Chips expand evenly; vertical scroll only.
        COLS = 2
        grid_w = QWidget()
        grid_w.setMinimumWidth(0)
        grid   = QGridLayout(grid_w)
        grid.setSpacing(6); grid.setContentsMargins(0, 0, 2, 0)
        for c in range(COLS):
            grid.setColumnStretch(c, 1)
            grid.setColumnMinimumWidth(c, 0)
        self._type_btns = {}
        for i, at in enumerate(ACTIVITY_TYPES):
            btn = QPushButton(f"{at['icon']} {at['label']}")
            btn.setCheckable(True)
            btn.setChecked(at["id"] == sel_type)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._apply_type_style(btn, at, at["id"] == sel_type)
            btn.clicked.connect(lambda _, aid=at["id"]: self._pick(aid))
            self._type_btns[at["id"]] = (btn, at)
            grid.addWidget(btn, i // COLS, i % COLS)
        type_scroll = QScrollArea()
        type_scroll.setWidgetResizable(True)
        type_scroll.setFrameShape(QFrame.Shape.NoFrame)
        type_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        type_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        type_scroll.setMinimumHeight(200)
        type_scroll.setMaximumHeight(280)
        type_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        type_scroll.setWidget(grid_w)
        # Keep the host from reporting a min width larger than the viewport
        # (that was clipping the old 3rd column with H-scroll off).
        grid_w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(type_scroll)

        # Times — respect the exact range the user dragged/clicked (24-hour display).
        # QTime only goes 00:00–23:59, so end-of-day (1440 / "24:00") is shown as
        # 00:00 and re-mapped on save when start is later the same day (see _save).
        trow = QHBoxLayout()
        end_min = max(end_min, start_min + 15)
        self.t_start = QTimeEdit(QTime(start_min // 60, start_min % 60))
        if end_min >= core.DAY_END:
            self.t_end = QTimeEdit(QTime(0, 0))   # display stand-in for 24:00
        else:
            self.t_end = QTimeEdit(QTime(end_min // 60, end_min % 60))
        self.t_start.setDisplayFormat("HH:mm")
        self.t_end.setDisplayFormat("HH:mm")
        self.t_end.setToolTip(
            "End time (24h). To run until midnight, set End to 00:00 when Start is "
            "later (saved as 24:00). Or drag the block to the bottom of the day.")
        for lbl, w in [("Start", self.t_start), ("End", self.t_end)]:
            col = QVBoxLayout()
            ql  = QLabel(lbl.upper())
            ql.setStyleSheet(f"color: {theme.C_MUTED.name()}; font-size: 10px;")
            col.addWidget(ql); col.addWidget(w)
            trow.addLayout(col)
        lay.addLayout(trow)

        # Optional title
        ql2 = QLabel("TITLE (optional)")
        ql2.setStyleSheet(f"color: {theme.C_MUTED.name()}; font-size: 10px;")
        lay.addWidget(ql2)
        self.txt = QLineEdit(placeholderText="What are you up to?")
        if is_edit:
            self.txt.setText(existing.get("title", ""))
        lay.addWidget(self.txt)

        # Buttons
        brow = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {theme.C_BORDER.name()};
            color: {theme.C_MUTED.name()}; padding: 8px 16px; border-radius: {theme.RAD}px; }}
            QPushButton:hover {{ color: {theme.C_TEXT.name()}; border-color: {theme.C_BORDER2.name()}; }}
        """)
        cancel.clicked.connect(self.reject)
        if is_edit:
            delete = QPushButton("Delete")
            delete.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: 1px solid {_rgba(theme.C_ERR, .5)};
                color: {theme.C_ERR_TXT.name()}; padding: 8px 16px; border-radius: {theme.RAD}px; }}
                QPushButton:hover {{ background: {_rgba(theme.C_ERR, .15)}; border-color: {theme.C_ERR.name()}; }}
            """)
            delete.clicked.connect(self._delete)
            brow.addWidget(delete)
        brow.addStretch()
        save = QPushButton("Save Changes" if is_edit else "Add to Schedule")
        save.setStyleSheet(f"""
            QPushButton {{ background: {theme.C_ACCENT.name()}; color: {theme.C_ON_ACCENT.name()}; padding: 8px 16px;
            border-radius: {theme.RAD}px; font-weight: bold; border: none; }}
            QPushButton:hover {{ background: {theme.C_ACCENT2.name()}; }}
        """)
        save.clicked.connect(self._save)
        brow.addWidget(cancel); brow.addWidget(save)
        lay.addLayout(brow)

    def _apply_type_style(self, btn, at, selected):
        style_activity_type_chip(btn, at, selected, compact=False)

    def _pick(self, type_id):
        self._sel = type_id
        for tid, (btn, at) in self._type_btns.items():
            btn.setChecked(tid == type_id)
            self._apply_type_style(btn, at, tid == type_id)

    def _save(self):
        st = self.t_start.time(); en = self.t_end.time()
        sm = st.hour() * 60 + st.minute()
        # Pass original end so a full-day 00:00–24:00 block can be re-saved
        # (both QTime fields show 00:00).
        orig_em = self._existing.get("endMin") if self._existing else None
        em = coerce_end_min(sm, en.hour() * 60 + en.minute(), original_end=orig_em)
        if em <= sm:
            QMessageBox.warning(
                self, "Invalid",
                "End must be after start.\n\n"
                "Tip: to run a block until midnight, set End to 00:00 "
                "(saved as end of day, 24:00).")
            return
        at = next((t for t in ACTIVITY_TYPES if t["id"] == self._sel), ACTIVITY_TYPES[0])
        self.result_activity = {
            "id": self._existing["id"] if self._existing else new_id(),
            "date": self._for_date,
            "startMin": sm, "endMin": em,
            "type": at["id"], "color": at["color"],
            "title": self.txt.text().strip() or f"{at['icon']} {at['label']}",
        }
        self.accept()

    def _delete(self):
        self.result_deleted = True
        self.accept()

# ══════════════════════════════════════════════════════════════════════════
#  SETUP SCREEN
# ══════════════════════════════════════════════════════════════════════════
class SetupWidget(QWidget):
    proceed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {theme.C_BG.name()};")
        outer = QVBoxLayout(self); outer.setAlignment(Qt.AlignCenter)

        card = QWidget(); card.setFixedWidth(520)
        card.setStyleSheet(f"""
            QWidget {{ background: {theme.C_SURFACE.name()}; border-radius: {theme.RAD_LG}px; color: {theme.C_TEXT.name()}; }}
            QLabel  {{ background: transparent; }}
        """)
        cl = QVBoxLayout(card); cl.setSpacing(14); cl.setContentsMargins(40,36,40,36)

        title = QLabel("📅 Daily Scheduler")
        title.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {theme.C_ACCENT.name()};")
        cl.addWidget(title)

        sub = QLabel("A native desktop app for planning your day.\n"
                     "Optionally connect Google Calendar or just use it offline.")
        sub.setStyleSheet(f"color: {theme.C_MUTED.name()}; font-size: 13px;"); sub.setWordWrap(True)
        cl.addWidget(sub)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {theme.C_BORDER.name()};"); cl.addWidget(sep)

        gcal = QLabel("Google Calendar (optional)")
        gcal.setStyleSheet("font-size: 13px; font-weight: bold;"); cl.addWidget(gcal)

        steps = QLabel(
            "1.  console.cloud.google.com → create project → enable Calendar API\n"
            "2.  APIs & Services → Credentials → + Create Credentials\n"
            "     → OAuth 2.0 Client ID → Desktop application → Download JSON\n"
            "3.  Load that file below — the app stores it in ~/.daily-scheduler/"
        )
        steps.setStyleSheet(f"color: {theme.C_MUTED.name()}; font-size: 12px;"); steps.setWordWrap(True)
        cl.addWidget(steps)

        have = core.CREDS_FILE.exists()
        self._creds_lbl = QLabel("✓ credentials.json loaded" if have else "No credentials loaded")
        self._creds_lbl.setStyleSheet(f"color: {theme.C_OK.name() if have else theme.C_MUTED.name()}; font-size: 12px;")
        cl.addWidget(self._creds_lbl)

        load_btn = QPushButton("Load credentials.json…")
        load_btn.setStyleSheet(f"""
            QPushButton {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
            color: {theme.C_TEXT.name()}; padding: 7px 14px; border-radius: {theme.RAD}px; font-size: 12px; border-style:solid; }}
            QPushButton:hover {{ border-color: {theme.C_BORDER2.name()}; }}
        """)
        load_btn.clicked.connect(self._load)
        cl.addWidget(load_btn)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color: {theme.C_BORDER.name()};"); cl.addWidget(sep2)

        ar = QHBoxLayout()
        go = QPushButton("Connect Google & Open")
        go.setStyleSheet(f"""
            QPushButton {{ background:{theme.C_ACCENT.name()}; color:{theme.C_ON_ACCENT.name()}; padding:9px 18px;
            border-radius:{theme.RAD}px; font-weight:bold; border:none; font-size:13px; }}
            QPushButton:hover {{ background:{theme.C_ACCENT2.name()}; }}
        """)
        go.clicked.connect(self._connect)

        skip = QPushButton("Use Without Google")
        skip.setStyleSheet(f"""
            QPushButton {{ background:transparent; border:1px solid {theme.C_BORDER.name()};
            color:{theme.C_MUTED.name()}; padding:9px 18px; border-radius:{theme.RAD}px; font-size:13px; }}
            QPushButton:hover {{ color:{theme.C_TEXT.name()}; border-color:{theme.C_BORDER2.name()}; }}
        """)
        skip.clicked.connect(self.proceed.emit)
        ar.addWidget(go); ar.addWidget(skip)
        cl.addLayout(ar)

        self._warn = QLabel(""); self._warn.setStyleSheet(f"color: {theme.C_ERR_TXT.name()}; font-size: 12px;")
        self._warn.setWordWrap(True); self._warn.hide(); cl.addWidget(self._warn)

        outer.addWidget(card)

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load credentials.json", "", "JSON (*.json)")
        if path:
            shutil.copy(path, str(core.CREDS_FILE))
            self._creds_lbl.setText("✓ credentials.json loaded")
            self._creds_lbl.setStyleSheet(f"color: {theme.C_OK.name()}; font-size: 12px;")

    def _connect(self):
        if not core.CREDS_FILE.exists():
            self._warn.setText("Please load your credentials.json first."); self._warn.show(); return
        self.proceed.emit()

# ══════════════════════════════════════════════════════════════════════════
#  ALERT POPUP — app-drawn, always-on-top. Bypasses the Windows notification
#  pipeline, so it shows even with Do Not Disturb / Focus Assist on.
#  Planner-style card (square tiles + left accent), not OS balloon chrome.
# ══════════════════════════════════════════════════════════════════════════
class AlertPopup(QWidget):
    def __init__(self, title, body, icon: QIcon, kind: str = "start"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        # Stable title so compositor window rules can target the popup — on Wayland
        # apps can't place their own windows, but e.g. a KWin rule matching this
        # title can force bottom-right + keep-above.
        self.setWindowTitle("Daily Scheduler Alert")
        self.setAttribute(Qt.WA_ShowWithoutActivating)   # don't steal focus
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)           # free itself when dismissed
        self.setFixedWidth(380)

        # kind: start | end | test — accent + badge text
        is_end = kind == "end"
        accent = theme.C_MUTED if is_end else theme.C_ACCENT
        badge  = "BLOCK ENDED" if is_end else ("TEST" if kind == "test" else "STARTING NOW")

        # Soft outer margin so a faux shadow rim stays inside the translucent window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        shell = QFrame(); shell.setObjectName("alertShell")
        shell.setStyleSheet(
            f"#alertShell {{ background: {_rgba(theme.C_BG, 0.55)}; border-radius: 6px; }}")
        outer.addWidget(shell)
        shell_l = QVBoxLayout(shell); shell_l.setContentsMargins(3, 3, 3, 3)

        card = QFrame(); card.setObjectName("alertCard")
        # Scope to #alertCard so rules don't cascade onto child QLabels (also QFrame).
        card.setStyleSheet(
            f"#alertCard {{ background: {theme.C_SURFACE.name()}; "
            f"border: 1px solid {_rgba(accent, 0.55)}; border-radius: 4px; }}")
        shell_l.addWidget(card)

        cl = QHBoxLayout(card); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)

        # Crisp left accent — same language as schedule blocks
        bar = QFrame(); bar.setFixedWidth(4)
        bar.setStyleSheet(f"background: {accent.name()}; border: none;")
        cl.addWidget(bar)

        col = QVBoxLayout(); col.setContentsMargins(14, 12, 14, 12); col.setSpacing(6)

        head = QHBoxLayout(); head.setSpacing(8)
        ic = QLabel(); ic.setPixmap(icon.pixmap(20, 20))
        app_lbl = QLabel("DAILY SCHEDULER")
        app_lbl.setStyleSheet(
            f"color: {theme.C_MUTED.name()}; font-size: 10px; font-weight: 700;"
            " letter-spacing: 1.2px;")
        badge_lbl = QLabel(badge)
        badge_lbl.setStyleSheet(
            f"color: {accent.name()}; background: {_rgba(accent, 0.14)}; "
            f"border: 1px solid {_rgba(accent, 0.35)}; border-radius: 3px; "
            f"padding: 2px 7px; font-size: 9px; font-weight: 700; letter-spacing: 0.6px;")
        head.addWidget(ic); head.addWidget(app_lbl); head.addStretch(); head.addWidget(badge_lbl)
        col.addLayout(head)

        t = QLabel(title); t.setWordWrap(True)
        t.setStyleSheet(
            f"color: {theme.C_TEXT.name()}; font-size: 15px; font-weight: 700; padding-top: 2px;")
        col.addWidget(t)

        b = QLabel(body); b.setWordWrap(True)
        b.setStyleSheet(f"color: {theme.C_MUTED.name()}; font-size: 12px; line-height: 1.3;")
        col.addWidget(b)

        foot = QLabel("Click to dismiss")
        foot.setStyleSheet(f"color: {_rgba(theme.C_MUTED, 0.75)}; font-size: 10px; padding-top: 2px;")
        col.addWidget(foot)
        cl.addLayout(col, 1)

        self._timer = QTimer(self); self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(12000)

    def show_at(self, x, y):
        self.adjustSize()
        self.move(x, y - self.height())
        self.show()  # no opacity effect — keeps the alert snappy under DND

    def mousePressEvent(self, _ev):
        self.close()


# ══════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════
class SettingsDialog(QDialog):
    """Central settings — persisted to settings.json. Most changes apply live; a
    theme change takes effect on the next launch."""
    def __init__(self, settings: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self.setMinimumHeight(520)
        self.resize(480, 640)
        self.values = dict(settings)
        self.startup_requested = is_startup_enabled()
        self.restored_acts: Optional[List[Dict]] = None
        self.setStyleSheet(f"""
            QDialog {{ background: {theme.C_SURFACE.name()}; color: {theme.C_TEXT.name()}; }}
            QLabel  {{ background: transparent; color: {theme.C_TEXT.name()}; }}
            QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QLineEdit {{
                background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
                color: {theme.C_TEXT.name()}; padding: 5px 8px; border-radius: {theme.RAD}px; }}
            QComboBox QAbstractItemView {{ background: {theme.C_SURFACE.name()}; color: {theme.C_TEXT.name()};
                selection-background-color: {theme.C_SURF2.name()}; }}
            QCheckBox {{ color: {theme.C_TEXT.name()}; spacing: 8px; }}
            QPushButton {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
                color: {theme.C_TEXT.name()}; padding: 6px 12px; border-radius: {theme.RAD}px; }}
            QPushButton:hover {{ border-color: {theme.C_BORDER2.name()}; }}
        """)
        # Scrollable body so added notify/AI rows still fit on short displays
        root = QVBoxLayout(self); root.setSpacing(0); root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        lay = QVBoxLayout(body); lay.setSpacing(8); lay.setContentsMargins(22, 18, 22, 12)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        def section(text, top=True):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color:{theme.C_MUTED.name()}; font-size:10px; font-weight:bold; "
                              f"letter-spacing:1px; margin-top:{10 if top else 0}px;")
            lay.addWidget(lbl)

        def hhmm_qtime(s):
            m = parse_hhmm(s)
            return QTime(m // 60, m % 60)

        section("GENERAL", top=False)
        g = QFormLayout(); g.setSpacing(8)
        self.theme_cb = QComboBox()
        for key, t in THEMES.items():
            self.theme_cb.addItem(t["label"], key)
        self.theme_cb.setCurrentIndex(max(0, self.theme_cb.findData(settings.get("theme", DEFAULT_THEME))))
        g.addRow("Theme", self.theme_cb)
        self.startup_cb = QCheckBox("Open Daily Scheduler when Windows starts")
        self.startup_cb.setChecked(is_startup_enabled())
        g.addRow("Startup", self.startup_cb)
        self.autostart_cb = QCheckBox("Start the Ollama server when the app launches")
        self.autostart_cb.setChecked(bool(settings.get("ollama_autostart")))
        g.addRow("AI server", self.autostart_cb)
        self.updates_cb = QCheckBox("Check for a newer version on launch")
        self.updates_cb.setChecked(bool(settings.get("update_check_on", True)))
        g.addRow("Updates", self.updates_cb)
        lay.addLayout(g)

        section("NOTIFICATIONS")
        n = QFormLayout(); n.setSpacing(8)
        self.notify_cb = QCheckBox("Alert me when a block starts")
        self.notify_cb.setChecked(bool(settings.get("notify_on")))
        n.addRow("Reminders", self.notify_cb)
        self.lead_sb = QSpinBox(); self.lead_sb.setRange(0, 60); self.lead_sb.setSuffix(" min before")
        self.lead_sb.setValue(int(settings.get("notify_lead_min", 0)))
        n.addRow("Lead time", self.lead_sb)
        self.end_chime_cb = QCheckBox("Chime when a block ends")
        self.end_chime_cb.setChecked(bool(settings.get("notify_end_chime", False)))
        self.end_chime_cb.setToolTip(
            "Optional: soft sound when a block ends (off by default; start alerts stay separate)")
        n.addRow("End chime", self.end_chime_cb)
        self.sound_cb = QCheckBox("Play a sound with alerts")
        self.sound_cb.setChecked(bool(settings.get("notify_sound", True)))
        n.addRow("Sound", self.sound_cb)
        self.tone_cb = QComboBox()
        for tid, label in NOTIFY_TONES:
            self.tone_cb.addItem(label, tid)
        cur_tone = str(settings.get("notify_tone", "chime") or "chime")
        ti = self.tone_cb.findData(cur_tone)
        self.tone_cb.setCurrentIndex(ti if ti >= 0 else 0)
        n.addRow("Tone", self.tone_cb)
        self.vol_sb = QSpinBox()
        self.vol_sb.setRange(0, 100)
        self.vol_sb.setSuffix("%")
        self.vol_sb.setValue(int(settings.get("notify_volume", 80) or 80))
        n.addRow("Volume", self.vol_sb)
        self.dnd_cb = QCheckBox("Break through Do Not Disturb / Focus Assist")
        self.dnd_cb.setChecked(bool(settings.get("dnd_override")))
        self.dnd_cb.setToolTip(
            "Uses an in-app popup (planner-style card) instead of the OS tray balloon, "
            "so alerts still show under Focus Assist / DND.")
        n.addRow("Priority alert", self.dnd_cb)
        preview_row = QHBoxLayout()
        prev_btn = QPushButton("Preview alert…")
        prev_btn.setToolTip("Show a sample popup and play the selected tone")
        prev_btn.clicked.connect(self._preview_alert)
        preview_row.addWidget(prev_btn); preview_row.addStretch()
        n.addRow("", preview_row)
        lay.addLayout(n)

        section("AI ASSISTANT")
        a = QFormLayout(); a.setSpacing(8)
        self.model_cb = QComboBox(); self.model_cb.setEditable(True)
        seen, models = set(), []
        for m in ai.list_ollama_models() + ai.RECOMMENDED_MODELS:
            if m and m not in seen:
                seen.add(m); models.append(m)
        self.model_cb.addItems(models)
        self.model_cb.setCurrentText(settings.get("model", DEFAULT_MODEL))
        self.model_cb.currentTextChanged.connect(self._on_settings_model_changed)
        a.addRow("Model", self.model_cb)
        self.model_hint = QLabel()
        self.model_hint.setWordWrap(True)
        self.model_hint.setStyleSheet(
            f"color:{theme.C_MUTED.name()}; font-size:11px; padding:2px 0 4px 0;")
        a.addRow("", self.model_hint)
        guide_btn = QPushButton("When to use each model…")
        guide_btn.setCursor(Qt.PointingHandCursor)
        guide_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.C_ACCENT.name()}; border: none;"
            f" text-align: left; padding: 0; font-size: 11px; }}"
            f"QPushButton:hover {{ text-decoration: underline; }}")
        guide_btn.clicked.connect(self._show_model_guide)
        a.addRow("", guide_btn)
        self._on_settings_model_changed(self.model_cb.currentText())
        self.temp_sb = QDoubleSpinBox(); self.temp_sb.setRange(0.0, 1.5); self.temp_sb.setSingleStep(0.1)
        self.temp_sb.setValue(float(settings.get("temperature", 0.3)))
        a.addRow("Temperature", self.temp_sb)
        self.ctx_cb = QComboBox()
        for v in (4096, 8192, 16384, 32768):
            self.ctx_cb.addItem(f"{v} tokens", v)
        self.ctx_cb.setCurrentIndex(max(0, self.ctx_cb.findData(int(settings.get("num_ctx", 16384)))))
        a.addRow("Context window", self.ctx_cb)
        self.pstart = QTimeEdit(hhmm_qtime(settings.get("plan_day_start", "08:00")))
        self.pend   = QTimeEdit(hhmm_qtime(settings.get("plan_day_end", "22:00")))
        for w in (self.pstart, self.pend):
            w.setDisplayFormat("HH:mm")
        wrow = QHBoxLayout(); wrow.setContentsMargins(0, 0, 0, 0)
        wrow.addWidget(self.pstart); wrow.addWidget(QLabel("to"))
        wrow.addWidget(self.pend); wrow.addStretch()
        ww = QWidget(); ww.setLayout(wrow)
        a.addRow("Planning hours", ww)

        # Where Ollama stores pulled models (OLLAMA_MODELS when this app starts Ollama)
        models_row = QHBoxLayout(); models_row.setContentsMargins(0, 0, 0, 0)
        self.models_dir = QLineEdit(str(settings.get("ollama_models_dir") or ""))
        self.models_dir.setPlaceholderText(str(default_ollama_models_dir()))
        self.models_dir.setToolTip(
            "Folder where Ollama saves downloaded models (sets OLLAMA_MODELS).\n"
            "Applies when Daily Scheduler starts Ollama. If Ollama is already "
            "running from the tray/service, restart it from this app (or quit "
            "the system Ollama first) so pulls use the new folder.\n"
            "Leave blank for Ollama’s default (~/.ollama/models).")
        browse_m = QPushButton("Browse…")
        browse_m.clicked.connect(self._browse_models_dir)
        open_m = QPushButton("Open")
        open_m.setToolTip("Open the effective models folder in your file manager")
        open_m.clicked.connect(self._open_models_dir)
        models_row.addWidget(self.models_dir, 1)
        models_row.addWidget(browse_m)
        models_row.addWidget(open_m)
        mw = QWidget(); mw.setLayout(models_row)
        a.addRow("Models folder", mw)
        models_hint = QLabel(
            "Custom path only takes effect when this app starts Ollama (▶ in the AI panel).")
        models_hint.setWordWrap(True)
        models_hint.setStyleSheet(f"color:{theme.C_MUTED.name()}; font-size:10px;")
        a.addRow("", models_hint)
        lay.addLayout(a)

        section("CALENDAR")
        c = QFormLayout(); c.setSpacing(8)
        self.cal_ids = QLineEdit(str(settings.get("calendar_ids", "primary")))
        self.cal_ids.setPlaceholderText("primary, or other calendar IDs, comma-separated")
        self.cal_ids.setToolTip(
            "Google Calendar IDs to overlay (read-only). Default is primary. "
            "Find IDs in Google Calendar → Settings → Integrate calendar. "
            "Example: primary,school@group.calendar.google.com")
        c.addRow("Calendar IDs", self.cal_ids)
        lay.addLayout(c)

        section("DATA")
        drow = QHBoxLayout()
        openf = QPushButton("Open data folder"); openf.clicked.connect(self._open_folder)
        expt  = QPushButton("Export schedule…"); expt.clicked.connect(self._export)
        rest  = QPushButton("Restore from backup…"); rest.clicked.connect(self._restore_backup)
        drow.addWidget(openf); drow.addWidget(expt); drow.addWidget(rest); drow.addStretch()
        lay.addLayout(drow)

        # Footer buttons stay outside the scroll area so Save is always reachable
        foot = QWidget()
        foot.setStyleSheet(f"background:{theme.C_SURFACE.name()}; border-top:1px solid {theme.C_BORDER.name()};")
        br = QHBoxLayout(foot); br.setContentsMargins(22, 10, 22, 14); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(f"QPushButton {{ background:{theme.C_ACCENT.name()}; color:{theme.C_ON_ACCENT.name()}; "
                           f"border:none; padding:7px 18px; border-radius:{theme.RAD}px; font-weight:bold; }}")
        save.clicked.connect(self._save)
        br.addWidget(cancel); br.addWidget(save)
        root.addWidget(foot)

    def _on_settings_model_changed(self, text):
        tip = model_when_text(text)
        have = model_is_installed(text)
        status = "Installed" if have else "Not installed (use ⬇ in the AI panel to pull)"
        self.model_hint.setText(f"{status}\n{tip}")
        self.model_cb.setToolTip(tip)

    def _show_model_guide(self):
        show_model_guide(self)

    def _browse_models_dir(self):
        start = self.models_dir.text().strip() or str(default_ollama_models_dir())
        path = QFileDialog.getExistingDirectory(self, "Ollama models folder", start)
        if path:
            self.models_dir.setText(path)

    def _open_models_dir(self):
        raw = self.models_dir.text().strip()
        p = Path(raw).expanduser() if raw else default_ollama_models_dir()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            if platform.system() == "Windows":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(p)])
            else:
                subprocess.run(["xdg-open", str(p)])
        except Exception:
            pass

    def _preview_alert(self):
        """Live sample of the current tone/volume/DND choices (does not Save)."""
        # Stash preview settings so MainWindow helpers can read them if parent is MainWindow
        tone = self.tone_cb.currentData() or "chime"
        vol  = self.vol_sb.value()
        sound = self.sound_cb.isChecked()
        parent = self.parent()
        # Play sound using the same synthesis path as production
        if sound and vol > 0:
            try:
                play_alert_sound(parent if isinstance(parent, QWidget) else self,
                                 tone=tone, volume=vol / 100.0)
            except Exception:
                pass
        icon = parent._make_app_icon() if parent is not None and hasattr(parent, "_make_app_icon") else QIcon()
        # Always show our planner-style card for preview (appearance is the point)
        popup = AlertPopup("▶ Study session", "14:00 – 15:00  ·  preview",
                           icon, kind="test")
        if not hasattr(self, "_preview_popups"):
            self._preview_popups = []
        self._preview_popups.append(popup)
        popup.destroyed.connect(
            lambda *_: self._preview_popups.remove(popup)
            if popup in getattr(self, "_preview_popups", []) else None)
        geo = QApplication.primaryScreen().availableGeometry()
        popup.show_at(geo.right() - popup.width() - 16, geo.bottom() - 16)

    def _open_folder(self):
        try:
            if platform.system() == "Windows":
                os.startfile(str(core.DATA_DIR))            # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(core.DATA_DIR)])
            else:
                subprocess.run(["xdg-open", str(core.DATA_DIR)])
        except Exception:
            pass

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export schedule", str(Path.home() / "daily-scheduler-export.json"),
            "JSON (*.json)")
        if path:
            try:
                shutil.copyfile(core.DATA_FILE, path)
            except Exception:
                pass

    def _restore_backup(self):
        """Pick a .bak or dated daily snapshot and stage it for MainWindow to apply."""
        items = list_schedule_backups()
        if not items:
            QMessageBox.information(
                self, "No backups",
                "No backup files found yet. Backups appear after you save schedule "
                "changes (previous-save .bak and daily snapshots under backups/).")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Restore from backup")
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            "Choose a restore point. Your current schedule will be replaced "
            "(a new .bak is written first if possible)."))
        lb = QComboBox()
        for it in items:
            lb.addItem(it["label"], str(it["path"]))
        lay.addWidget(lb)
        row = QHBoxLayout(); row.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(dlg.reject)
        ok = QPushButton("Restore")
        ok.setStyleSheet(
            f"QPushButton {{ background:{theme.C_ACCENT.name()}; color:{theme.C_ON_ACCENT.name()}; "
            f"border:none; padding:7px 18px; border-radius:{theme.RAD}px; font-weight:bold; }}")
        ok.clicked.connect(dlg.accept)
        row.addWidget(cancel); row.addWidget(ok)
        lay.addLayout(row)
        if dlg.exec() != QDialog.Accepted:
            return
        path = Path(lb.currentData())
        acts = load_activities_from_path(path)
        if acts is None:
            QMessageBox.warning(self, "Restore failed",
                                f"Could not read a valid schedule from:\n{path}")
            return
        confirm = QMessageBox.question(
            self, "Confirm restore",
            f"Replace your current schedule with:\n\n{lb.currentText()}\n\n"
            f"({len(acts)} block(s))",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        self.restored_acts = acts
        # Close settings with Accept so MainWindow applies the restore + saves settings
        self._save()

    def _save(self):
        self.startup_requested = self.startup_cb.isChecked()
        self.values.update({
            "theme":            self.theme_cb.currentData(),
            "ollama_autostart": self.autostart_cb.isChecked(),
            "ollama_models_dir": self.models_dir.text().strip(),
            "update_check_on":  self.updates_cb.isChecked(),
            "notify_on":        self.notify_cb.isChecked(),
            "notify_lead_min":  self.lead_sb.value(),
            "notify_end_chime": self.end_chime_cb.isChecked(),
            "notify_sound":     self.sound_cb.isChecked(),
            "notify_tone":      self.tone_cb.currentData() or "chime",
            "notify_volume":    int(self.vol_sb.value()),
            "dnd_override":     self.dnd_cb.isChecked(),
            "model":            self.model_cb.currentText().strip() or DEFAULT_MODEL,
            "temperature":      round(self.temp_sb.value(), 2),
            "num_ctx":          self.ctx_cb.currentData(),
            "plan_day_start":   self.pstart.time().toString("HH:mm"),
            "plan_day_end":     self.pend.time().toString("HH:mm"),
            "calendar_ids":     self.cal_ids.text().strip() or "primary",
        })
        self.accept()
