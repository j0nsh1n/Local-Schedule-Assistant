#!/usr/bin/env python3
"""
Daily Scheduler — Native Desktop App
Pure Python + PySide6 (Qt6). No browser engine.

Copyright (C) 2026 Jonathan Shin

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import sys
import json
import uuid
import shutil
import os
import getpass
import platform
import subprocess
import re
import time
import traceback
import faulthandler
import calendar as _cal
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict

import requests
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QScrollArea, QFrame,
    QDialog, QFileDialog, QTimeEdit, QStackedWidget, QSizePolicy,
    QMessageBox, QMenu, QGridLayout, QProgressBar, QSystemTrayIcon,
    QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox, QFormLayout,
    QGraphicsOpacityEffect, QSplitter,
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QRect, QTime, QSharedMemory, QUrl,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QFontMetrics,
    QPalette, QPixmap, QIcon, QDesktopServices, QKeySequence, QShortcut,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# ── App metadata ───────────────────────────────────────────────────────────
__version__  = "4.0.0"
APP_VERSION  = __version__

# Auto-update check (roadmap #2): compare the newest GitHub release's tag against
# APP_VERSION once per launch + daily. Returns 404 while the repo is PRIVATE — the
# check fails silently and simply lights up the day the repo goes public.
GITHUB_REPO        = "j0nsh1n/Local-Schedule-Assistant"
RELEASES_PAGE      = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# ── App data paths ─────────────────────────────────────────────────────────
DATA_DIR   = Path.home() / ".daily-scheduler"
DATA_FILE  = DATA_DIR / "activities.json"
CREDS_FILE = DATA_DIR / "credentials.json"
TOKEN_FILE = DATA_DIR / "token.json"
CRASH_LOG  = DATA_DIR / "crash.log"   # native fatal faults (faulthandler)
ERROR_LOG  = DATA_DIR / "app.log"     # unhandled Python tracebacks (sys.excepthook)
CHAT_FILE  = DATA_DIR / "chat.json"   # v3.8.0: AI panel transcript (crash-proof)
CHAT_SAVE_MIN_SEC = 0.4               # throttle mid-stream writes
DATA_DIR.mkdir(exist_ok=True)

# ── Layout constants ───────────────────────────────────────────────────────
DAY_START_H = 0
DAY_END_H   = 24
DAY_START   = DAY_START_H * 60   # minutes from midnight (full 24h day)
DAY_END     = DAY_END_H   * 60
HOUR_PX     = 96                  # pixels per hour on timeline (scrolls; centers on now)
GUTTER_W    = 64                  # width of time-label column
OLLAMA_URL  = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:14b"     # better at tool-use/reasoning than llama3.1:8b
# Curated picks that fit a ~16 GB GPU and are strong at tool-calling (this app is
# tool-heavy). Keys are ollama pull tags; shown in the model picker alongside
# whatever `ollama list` reports. `when` is user-facing guidance in Settings /
# the AI panel tooltip — keep each blurb one short paragraph.
MODEL_PROFILES = {
    "qwen3:14b": {
        "badge": "★ Best everyday",
        "vram": "~10 GB",
        "disk": "~9.3 GB",
        "when": (
            "Default recommendation. Reliable tool-calling with context headroom "
            "on 12–16 GB GPUs — use this as your daily driver for planning and edits."
        ),
    },
    "mistral-small3.1:24b": {
        "badge": "Strongest (tight fit)",
        "vram": "~15 GB",
        "disk": "~15 GB",
        "when": (
            "Excellent tool-calling when you want the best quality. Needs ~15 GB "
            "VRAM — a tight fit on 16 GB cards; unload other GPU apps first."
        ),
    },
    "qwen2.5:14b": {
        "badge": "Solid fallback",
        "vram": "~10 GB",
        "disk": "~9 GB",
        "when": (
            "Previous default — still very capable at tools. Use if qwen3 "
            "misbehaves or you already have it pulled."
        ),
    },
    "gpt-oss:20b": {
        "badge": "OpenAI open weights",
        "vram": "~13–14 GB",
        "disk": "~13 GB",
        "when": (
            "OpenAI's open MoE model. Capable generalist; verify tool-calling "
            "in-app on a few plan/edit requests before trusting multi-step rebuilds."
        ),
    },
    "deepseek-r1:14b": {
        "badge": "Deep reasoning",
        "vram": "~10 GB",
        "disk": "~9 GB",
        "when": (
            "Reasoning model that thinks before acting — useful for complex "
            "\"plan my week\" questions, but slower and may narrate instead of "
            "calling tools. The app strips its <think> blocks automatically."
        ),
    },
    "gemma4": {
        "badge": "Try / verify first",
        "vram": "~10 GB",
        "disk": "~9.6 GB",
        "when": (
            "Google's Gemma 4 (default tag ≈ e4b). Capable with native tools, but "
            "less battle-tested here than Qwen — try a few plan/edit requests "
            "before making it your daily driver."
        ),
    },
    "glm-4.7-flash": {
        "badge": "Large MoE (needs VRAM)",
        "vram": "~16+ GB",
        "disk": "~19 GB",
        "when": (
            "30B-class MoE — strong and relatively fast when it fits fully on GPU. "
            "Default quant is ~19 GB on disk, so 16 GB cards may offload to RAM "
            "(slower). Verify tool-calling before bulk schedule rebuilds."
        ),
    },
}
RECOMMENDED_MODELS = list(MODEL_PROFILES.keys())

# ── Theme system (v4.0) ────────────────────────────────────────────────────
# Planner-first look (not Google Calendar cards): square schedule blocks, crisp
# grid, modest chrome radii only on buttons/dialogs. Amber dark / ink light.
THEMES = {
    "nocturne": {   # high-contrast dark planner
        "label": "Nocturne — dark",
        "bg": "#0b0b0d", "surface": "#141418", "surf2": "#1c1c22",
        "border": "#2c2c34", "border2": "#40404c",
        "text": "#f2f2f4", "muted": "#8e8e98",
        "accent": "#e8b84a", "accent2": "#c9962e", "on_accent": "#0b0b0d",
        "now": "#f07167", "grid": "#1a1a20", "ghost": "#3a3a44",
        "ok": "#5fbf85", "ok_txt": "#8fd9a8", "err": "#f07167", "err_txt": "#f5a8a2",
        "warn": "#e8b84a", "info": "#6b8cae",   # muted steel, not GCal blue
        "rad": 4, "rad_lg": 6, "mono": True,
    },
    "slate": {      # paper-light planner
        "label": "Slate — light",
        "bg": "#f0f0ee", "surface": "#fafaf8", "surf2": "#e8e8e4",
        "border": "#d4d4ce", "border2": "#b8b8b0",
        "text": "#1a1a18", "muted": "#5c5c56",
        "accent": "#b45309", "accent2": "#92400e", "on_accent": "#fffbeb",
        "now": "#c2410c", "grid": "#e4e4de", "ghost": "#c4c4bc",
        "ok": "#2ba37e", "ok_txt": "#1e7a5e", "err": "#b91c1c", "err_txt": "#991b1b",
        "warn": "#b45309", "info": "#57534e",
        "rad": 4, "rad_lg": 6, "mono": False,
    },
}
DEFAULT_THEME = "nocturne"

# Chrome colour globals — (re)assigned by apply_theme(); initialised at import.
C_BG = C_SURFACE = C_SURF2 = C_BORDER = C_BORDER2 = None
C_TEXT = C_MUTED = C_ACCENT = C_ACCENT2 = C_ON_ACCENT = C_NOW = None
C_GRID = C_GHOST = C_OK = C_OK_TXT = C_ERR = C_ERR_TXT = C_WARN = C_INFO = None
RAD = RAD_LG = 0
THEME_MONO = False
THEME_NAME = DEFAULT_THEME

def _rgba(color, alpha) -> str:
    """'rgba(r,g,b,a)' from a QColor (or hex) + 0..1 alpha — for hover/fill tints
    that must follow the active theme."""
    c = color if isinstance(color, QColor) else QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"

def apply_theme(name: str):
    """Point every C_* global at the named theme. Call before building the UI."""
    global C_BG, C_SURFACE, C_SURF2, C_BORDER, C_BORDER2, C_TEXT, C_MUTED
    global C_ACCENT, C_ACCENT2, C_ON_ACCENT, C_NOW, C_GRID, C_GHOST
    global C_OK, C_OK_TXT, C_ERR, C_ERR_TXT, C_WARN, C_INFO
    global RAD, RAD_LG, THEME_MONO, THEME_NAME
    THEME_NAME = name if name in THEMES else DEFAULT_THEME
    t = THEMES[THEME_NAME]
    C_BG        = QColor(t["bg"]);        C_SURFACE   = QColor(t["surface"])
    C_SURF2     = QColor(t["surf2"]);     C_BORDER    = QColor(t["border"])
    C_BORDER2   = QColor(t["border2"]);   C_TEXT      = QColor(t["text"])
    C_MUTED     = QColor(t["muted"]);     C_ACCENT    = QColor(t["accent"])
    C_ACCENT2   = QColor(t["accent2"]);   C_ON_ACCENT = QColor(t["on_accent"])
    C_NOW       = QColor(t["now"]);       C_GRID      = QColor(t["grid"])
    C_GHOST     = QColor(t["ghost"])
    C_OK        = QColor(t["ok"]);        C_OK_TXT    = QColor(t["ok_txt"])
    C_ERR       = QColor(t["err"]);       C_ERR_TXT   = QColor(t["err_txt"])
    C_WARN      = QColor(t["warn"]);      C_INFO      = QColor(t["info"])
    RAD         = t["rad"];               RAD_LG      = t["rad_lg"]
    THEME_MONO  = t["mono"]

def app_chrome_stylesheet() -> str:
    """Global widget chrome for a more modern, cohesive look (applied once at launch)."""
    return f"""
        QToolTip {{
            background: {C_SURFACE.name()}; color: {C_TEXT.name()};
            border: 1px solid {C_BORDER2.name()}; border-radius: {RAD}px;
            padding: 6px 8px; font-size: 11px;
        }}
        QScrollBar:vertical {{
            background: transparent; width: 10px; margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {_rgba(C_MUTED, .35)}; border-radius: 5px; min-height: 32px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {_rgba(C_MUTED, .55)}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{
            background: transparent; height: 10px; margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {_rgba(C_MUTED, .35)}; border-radius: 5px; min-width: 32px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    """

apply_theme(DEFAULT_THEME)

# ── Activity types ─────────────────────────────────────────────────────────
# Expanded set for high-school life. Tool schemas + AI prompt are generated from
# this list, so adding a type here is enough for pickers and the model.
ACTIVITY_TYPES = [
    {"id": "assignments", "label": "Assignments",      "icon": "📝", "color": "#ef4444"},
    {"id": "project",     "label": "Projects",         "icon": "🛠",  "color": "#f59e0b"},
    {"id": "study",       "label": "Study",            "icon": "📚", "color": "#8b5cf6"},
    {"id": "class",       "label": "Class / School",   "icon": "🏫", "color": "#3b82f6"},
    {"id": "reading",     "label": "Reading",          "icon": "📖", "color": "#a78bfa"},
    {"id": "extra",       "label": "Extracurriculars", "icon": "🎯", "color": "#ec4899"},
    {"id": "club",        "label": "Clubs",            "icon": "🏛", "color": "#d946ef"},
    {"id": "music",       "label": "Music / Practice", "icon": "🎵", "color": "#14b8a6"},
    {"id": "creative",    "label": "Creative / Art",   "icon": "🎨", "color": "#f472b6"},
    {"id": "gaming",      "label": "Anime/Gaming",     "icon": "🎮", "color": "#06b6d4"},
    {"id": "social",      "label": "Social",           "icon": "👥", "color": "#22d3ee"},
    {"id": "exercise",    "label": "Exercise",         "icon": "💪", "color": "#10b981"},
    {"id": "meals",       "label": "Meals",            "icon": "🍽", "color": "#f97316"},
    {"id": "chores",      "label": "Chores",           "icon": "🏠", "color": "#a3a3a3"},
    {"id": "work",        "label": "Work / Job",       "icon": "💼", "color": "#64748b"},
    {"id": "commute",     "label": "Commute",          "icon": "🚌", "color": "#78716c"},
    {"id": "health",      "label": "Health",           "icon": "🏥", "color": "#fb7185"},
    {"id": "free",        "label": "Free / Rest",      "icon": "☕", "color": "#94a3b8"},
    {"id": "sleep",       "label": "Sleep",            "icon": "🌙", "color": "#6366f1"},
]

# Map legacy type ids (from older data) onto the current set, so existing blocks
# keep a sensible category/color after this change.
_OLD_TYPE_MAP = {"anime": "gaming", "friends": "extra", "social": "social",
                 "gym": "exercise", "workout": "exercise", "rest": "free",
                 "break": "free", "school": "class", "lesson": "class"}

def activity_type_prompt_block() -> str:
    """Human lines for the AI system prompt — always stays in sync with ACTIVITY_TYPES."""
    lines = [
        "ACTIVITY TYPES — set each block's \"type\" to what the user will actually be "
        "DOING (judge by the activity itself, not the blocks around it):"
    ]
    for t in ACTIVITY_TYPES:
        lines.append(f"  {t['id']:<12} – {t['label']}")
    lines += [
        "TYPE RULES (the model often gets these wrong — follow them):",
        "  - A BREAK or REST between work → use \"free\" (or \"gaming\" for entertainment,",
        "    \"exercise\" for a physical break, \"meals\" for a snack). NEVER label a break",
        "    as \"study\", \"assignments\", \"project\", or \"class\".",
        "  - A break between two study blocks is still a break — don't copy the surrounding type.",
        "  - split_block focus chunks keep the task type; breaks default to \"free\"",
        "    (override with break_type).",
        "  - School lessons / periods → \"class\". Homework due soon → \"assignments\".",
        "  - Hangouts → \"social\". Band/orchestra practice → \"music\".",
    ]
    return "\n".join(lines)

# ── Pure helper functions ──────────────────────────────────────────────────
def min_to_y(minutes: int) -> int:
    return int((minutes - DAY_START) / 60 * HOUR_PX)

def y_to_min(y: int) -> int:
    return int(DAY_START + y / HOUR_PX * 60)

# Calendar-block color recipe — chips / pickers must use the same alphas so a
# selected activity type looks like the box that will land on the timeline.
BLOCK_FILL_A = 52          # translucent body fill (0–255)
BLOCK_OUTLINE_A = 160      # 1px outline around the tile
BLOCK_FILL_CSS = BLOCK_FILL_A / 255.0
BLOCK_OUTLINE_CSS = BLOCK_OUTLINE_A / 255.0

def block_colors(hex_color: str) -> tuple:
    """(accent solid QColor, translucent fill QColor) for a category hex."""
    c = QColor(hex_color or C_ACCENT.name())
    fill = QColor(c.red(), c.green(), c.blue(), BLOCK_FILL_A)
    return c, fill

def style_activity_type_chip(btn, at: dict, selected: bool, *, compact: bool = False):
    """Style a type-picker chip to match the calendar block recipe:
    solid left accent + translucent category fill + category-colored label when
    selected; unselected chips still show a thin left accent so each type’s
    calendar color is obvious before you pick it.

    Chips must be able to shrink with the grid (min-width: 0) so a multi-column
    layout never forces the parent wider than the dialog/sidebar and clips."""
    c = at["color"]
    # Sidebar (compact) and dialog chips — readable type labels without
    # blowing out the grid (was 9px / 11px and felt tiny with 19 types).
    pad = "5px 6px" if compact else "6px 7px"
    fsz = "11px" if compact else "12px"
    rad = "3px" if compact else f"{RAD}px"
    # Expanding + min 0: QGridLayout can share width evenly; long labels elide
    # via the button's own clipping rather than overflowing the panel.
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    btn.setMinimumWidth(0)
    btn.setToolTip(at.get("label", ""))
    if selected:
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {_rgba(c, BLOCK_FILL_CSS)};
                border: 1px solid {_rgba(c, BLOCK_OUTLINE_CSS)};
                border-left: 3px solid {c};
                color: {c};
                font-weight: bold;
                padding: {pad};
                border-radius: {rad};
                font-size: {fsz};
                text-align: left;
                min-width: 0;
            }}
        """)
    else:
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {C_SURF2.name()};
                border: 1px solid {C_BORDER.name()};
                border-left: 3px solid {c};
                color: {C_MUTED.name()};
                padding: {pad};
                border-radius: {rad};
                font-size: {fsz};
                text-align: left;
                min-width: 0;
            }}
            QPushButton:hover {{
                background: {_rgba(c, BLOCK_FILL_CSS * 0.55)};
                border-color: {_rgba(c, BLOCK_OUTLINE_CSS * 0.7)};
                border-left: 3px solid {c};
                color: {C_TEXT.name()};
            }}
        """)

def paint_schedule_block(p: QPainter, rect: QRect, fill: QColor, accent: QColor,
                         radius: int = 0, accent_w: int = 3, outline: bool = False):
    """Square planner tiles (deliberately not GCal-style rounded cards): solid fill,
    1px outline, crisp left accent bar. `radius` is ignored (kept for call-site
    compatibility)."""
    p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), BLOCK_OUTLINE_A), 1))
    p.setBrush(fill)
    p.drawRect(rect.adjusted(0, 0, -1, -1))
    if accent_w > 0:
        p.fillRect(QRect(rect.x(), rect.y(), accent_w, rect.height()), accent)
    if outline:
        p.setPen(QPen(accent, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect.adjusted(1, 1, -2, -2))

def fmt_time(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)   # 24-hour HH:MM (e.g. 09:00, 14:30, 24:00)
    return f"{h:02d}:{m:02d}"

def fmt_dur(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"

def strip_v(tag: str) -> str:
    """'v3.2.0' → '3.2.0'; leaves an already-bare version untouched."""
    t = (tag or "").strip()
    return t[1:] if t[:1] in ("v", "V") else t

def _version_tuple(s: str) -> tuple:
    """('v3.1.0' | '3.1.0' | '3.1.0-beta.2') → (3, 1, 0). Strips a leading 'v' and
    any '-'/'+' pre-release or build suffix, then parses the dotted integers.
    Returns () when nothing parseable remains (so garbage never ranks as an update)."""
    core = strip_v(s).split("-")[0].split("+")[0].strip()
    if not core:
        return ()
    try:
        return tuple(int(p) for p in core.split("."))
    except ValueError:
        return ()

def is_newer_version(latest: str, current: str) -> bool:
    """True iff release tag `latest` is a strictly newer version than `current`.
    Fails CLOSED: an unparseable/empty `latest` returns False so we never nag on
    a malformed tag. Shorter versions are zero-padded ('3.2' == '3.2.0')."""
    lt = _version_tuple(latest)
    if not lt:
        return False
    ct = _version_tuple(current)
    n = max(len(lt), len(ct))
    lt += (0,) * (n - len(lt))
    ct += (0,) * (n - len(ct))
    return lt > ct

def now_next_summary(blocks: List[Dict], now_min: int) -> str:
    """One-line 'Now / Next' status for minute-of-day `now_min`. `blocks` = today's
    items (each with startMin/endMin/title). Returns '' when nothing is current OR
    upcoming (e.g. after the last block). Pure so it's unit-testable without a clock."""
    def short(b) -> str:
        t = (b.get("title") or "").strip() or "Untitled"
        return t if len(t) <= 30 else t[:29] + "…"
    ordered = sorted(blocks, key=lambda b: b["startMin"])
    cur = next((b for b in ordered if b["startMin"] <= now_min < b["endMin"]), None)
    # "Next" is the block starting after the current one ENDS — skip blocks that merely
    # overlap the current one (user blocks and calendar events mix here, so overlaps are
    # realistic). With no current block, it's the next block starting after now.
    after = cur["endMin"] if cur else now_min
    nxt = next((b for b in ordered if b["startMin"] > now_min and b["startMin"] >= after), None)
    parts = []
    if cur:
        parts.append(f"Now: {short(cur)} · {fmt_dur(cur['endMin'] - now_min)} left")
    if nxt:
        when = fmt_time(nxt["startMin"])
        if cur:
            parts.append(f"Next: {short(nxt)} at {when}")
        else:
            parts.append(f"Next: {short(nxt)} at {when} (in {fmt_dur(nxt['startMin'] - now_min)})")
    return "  →  ".join(parts)

def is_all_day_event(e: Dict) -> bool:
    """True for Google all-day (and multi-day) events — they do not occupy a time span."""
    return bool(e.get("allDay"))

def timed_cal_events(events: List[Dict]) -> List[Dict]:
    return [e for e in events if not is_all_day_event(e)]

def allday_cal_events(events: List[Dict]) -> List[Dict]:
    return [e for e in events if is_all_day_event(e)]

def format_cal_event_brief(e: Dict) -> str:
    """One short phrase for AI / banners: timed with HH:MM range, all-day labeled."""
    title = e.get("title") or "(no title)"
    if is_all_day_event(e):
        return f"{title} (all day)"
    return f"{title} {fmt_time(e['startMin'])}–{fmt_time(e['endMin'])}"

def normalize_google_event(ev: dict) -> List[Dict]:
    """Turn one Google Calendar API event into 0+ day-scoped dicts used in
    `_cal_by_date`. Timed events → one entry; all-day → one entry per day in the
    half-open [start.date, end.date) range Google uses. Pure (no network)."""
    title = ev.get("summary") or "(no title)"
    eid   = ev.get("id") or new_id()
    start = ev.get("start") or {}
    end   = ev.get("end") or {}
    color = C_INFO.name()

    if start.get("dateTime"):
        s_raw = start["dateTime"]
        e_raw = end.get("dateTime") or s_raw
        try:
            s  = datetime.fromisoformat(s_raw.replace("Z", "+00:00")).astimezone()
            en = datetime.fromisoformat(e_raw.replace("Z", "+00:00")).astimezone()
        except Exception:
            return []
        sm = max(s.hour * 60 + s.minute, DAY_START)
        em = min(en.hour * 60 + en.minute, DAY_END)
        if em <= sm:
            return []
        ds = s.date().isoformat()
        return [{
            "id": eid, "title": title, "startMin": sm, "endMin": em,
            "type": "calendar", "color": color, "date": ds, "allDay": False,
        }]

    # All-day: start.date / end.date (end exclusive). Multi-day holidays expand.
    d0s = start.get("date")
    if not d0s:
        return []
    try:
        d0 = date.fromisoformat(d0s)
        d1s = end.get("date") or d0s
        d1 = date.fromisoformat(d1s)
    except Exception:
        return []
    if d1 <= d0:
        d1 = d0 + timedelta(days=1)
    out = []
    d = d0
    while d < d1:
        ds = d.isoformat()
        out.append({
            "id": f"{eid}:{ds}", "title": title,
            "startMin": 0, "endMin": 0,   # not a timed span — filtered from free slots
            "type": "calendar", "color": color, "date": ds, "allDay": True,
        })
        d += timedelta(days=1)
    return out

def week_ahead_lines(cal_by_date: Dict[str, List[Dict]], start: date, days: int = 7) -> str:
    """Compact multi-day calendar preview for the AI system prompt: read-only Google
    events over [start, start+days), one line per day, days with no events omitted.
    `start` is the anchor (today) so offset 0/1 label as today/tomorrow — pure (no clock
    read), so it's unit-testable. Returns '' when nothing is scheduled in the window.
    All-day events render as 'Title (all day)'; timed keep HH:MM–HH:MM."""
    out = []
    for i in range(days):
        d  = start + timedelta(days=i)
        raw = cal_by_date.get(d.isoformat(), [])
        if not raw:
            continue
        # All-day first (deadlines/holidays), then timed by start.
        ad  = allday_cal_events(raw)
        tm  = sorted(timed_cal_events(raw), key=lambda e: e["startMin"])
        ev  = ad + tm
        label = {0: "today", 1: "tomorrow"}.get(i, d.strftime("%a %b %d"))
        items = "; ".join(format_cal_event_brief(e) for e in ev)
        out.append(f"  {d.isoformat()} ({label}): {items}")
    return "\n".join(out)

def today_str() -> str:
    return date.today().isoformat()

def new_id() -> str:
    return str(uuid.uuid4())[:8]

# ── Crash / error logging ───────────────────────────────────────────────────
# A "flight recorder" for a --windowed app that has no console: persist WHY it died.
#   app.log   — unhandled Python tracebacks (sys.excepthook), rotated at ~1 MB → .old
#   crash.log — native fatal faults (segfault/abort, e.g. a GPU-driver crash) via
#               faulthandler, dumping every thread's stack.
# Privacy: tracebacks + faulthandler dumps record frames/lines only, NEVER local values,
# so no schedule data is written (proven by test_error_log.py). All best-effort — a
# logging failure must never block startup or silence the app.
_crash_fh = None   # crash.log kept open for the process lifetime (faulthandler writes to it)

def _rotate_log(path: Path, max_bytes: int = 1_000_000) -> None:
    """Single-generation rotation: once `path` passes max_bytes, move it to `<path>.old`
    (replacing any previous .old) so the log can't grow without bound."""
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            path.replace(path.with_name(path.name + ".old"))
    except Exception:
        pass

def log_exception(exc_type, exc, tb) -> None:
    """sys.excepthook: append an unhandled exception's traceback to app.log (rotated
    first), then defer to the default hook so it still reaches stderr. Records the code
    path only — no local variables — so schedule data never leaks."""
    try:
        _rotate_log(ERROR_LOG)
        with ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} "
                    f"· v{APP_VERSION} · pid {os.getpid()} =====\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)

def install_crash_logging() -> None:
    """Wire up the two diagnostics as early as possible in main(). Best-effort: any
    failure here is swallowed so it can never keep the app from starting."""
    global _crash_fh
    try:
        _rotate_log(CRASH_LOG)
        _crash_fh = CRASH_LOG.open("a", encoding="utf-8")   # held open for the process life
        faulthandler.enable(file=_crash_fh, all_threads=True)
    except Exception:
        pass
    sys.excepthook = log_exception

# ── Local storage ──────────────────────────────────────────────────────────
def _migrate_types(acts: List[Dict]) -> List[Dict]:
    """Remap any legacy/unknown activity type onto the current set and refresh the
    block's colour to match the current palette. Runs silently on load."""
    by_id = {t["id"]: t for t in ACTIVITY_TYPES}
    for a in acts:
        tid = a.get("type")
        tid = _OLD_TYPE_MAP.get(tid, tid)
        if tid not in by_id:
            tid = "study"
        a["type"]  = tid
        a["color"] = by_id[tid]["color"]
    return acts

def load_all_activities() -> List[Dict]:
    try:
        return _migrate_types(json.loads(DATA_FILE.read_text()))
    except Exception:
        return []

def save_all_activities(acts: List[Dict]) -> None:
    try:
        # Rotate the outgoing state to .bak first. The dated daily backup below is
        # overwritten by every save, and the in-memory AI-undo stack dies with the
        # process — so without this, one bad save after a restart was unrecoverable.
        # .bak always lags the live file by exactly one save.
        if DATA_FILE.exists():
            shutil.copyfile(DATA_FILE, DATA_FILE.with_name("activities.json.bak"))
    except Exception:
        pass
    try:
        DATA_FILE.write_text(json.dumps(acts, indent=2))
        _write_daily_backup(acts)
    except Exception:
        pass

# ── Rolling daily backups ────────────────────────────────────────────────────
# Safety net against a bad edit / corrupt write, two layers:
#   activities.json.bak                — the state before the MOST RECENT save
#                                        (recovers a single bad save, even after
#                                        a restart wiped the in-memory AI undo)
#   backups/activities-YYYY-MM-DD.json — latest state per day, newest BACKUP_KEEP
#                                        kept (recovers across days)
BACKUP_DIR  = DATA_DIR / "backups"
BACKUP_KEEP = 14
BAK_FILE    = DATA_DIR / "activities.json.bak"
MANUAL_UNDO_KEEP = 24   # v4.0: Ctrl+Z for manual edits

def _write_daily_backup(acts: List[Dict]) -> None:
    """Best-effort: one snapshot per day (latest state of that day); prune to the
    newest BACKUP_KEEP. Never let a backup failure disrupt the real save."""
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        (BACKUP_DIR / f"activities-{date.today().isoformat()}.json").write_text(
            json.dumps(acts, indent=2))
        for old in sorted(BACKUP_DIR.glob("activities-*.json"))[:-BACKUP_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass

def list_schedule_backups() -> List[Dict]:
    """Discover restore points: .bak + dated dailies. Pure filesystem; no schedule
    contents loaded. Each item: {path, label, mtime, kind}."""
    out: List[Dict] = []
    try:
        if BAK_FILE.exists():
            st = BAK_FILE.stat()
            out.append({
                "path": BAK_FILE, "kind": "previous",
                "label": f"Previous save  ·  {datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')}",
                "mtime": st.st_mtime,
            })
    except OSError:
        pass
    try:
        for p in sorted(BACKUP_DIR.glob("activities-*.json"), reverse=True):
            try:
                st = p.stat()
                day = p.stem.replace("activities-", "", 1)
                out.append({
                    "path": p, "kind": "daily",
                    "label": f"Daily snapshot  ·  {day}  ·  {datetime.fromtimestamp(st.st_mtime).strftime('%H:%M')}",
                    "mtime": st.st_mtime,
                })
            except OSError:
                continue
    except OSError:
        pass
    out.sort(key=lambda x: -x["mtime"])
    return out

def load_activities_from_path(path: Path) -> Optional[List[Dict]]:
    """Load + migrate activities from a backup file. None if unreadable/invalid."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None
        return _migrate_types(data)
    except Exception:
        return None

def parse_calendar_ids(s: str) -> List[str]:
    """Comma-separated Google calendar IDs → non-empty list (default primary)."""
    ids = [x.strip() for x in str(s or "").split(",") if x.strip()]
    return ids or ["primary"]

# ── AI undo ──────────────────────────────────────────────────────────────────
# The assistant can rewrite or clear whole days, so snapshot the schedule before
# the first schedule-changing tool of each AI turn; "Undo" restores the snapshot.
AI_UNDO_KEEP      = 12
AI_READONLY_TOOLS = frozenset({"list_blocks", "find_free_time", "week_summary"})

# ── Notification de-dup (cross-process) ──────────────────────────────────────
# A block alert must fire EXACTLY ONCE per day — even if more than one copy of the
# app is running, each with its own 20 s notify timer (e.g. a second instance that
# slipped past the single-instance guard at boot, where Windows launches several
# copies at once). The in-memory `_notified` set only dedups within one process;
# these atomic marker files dedup ACROSS processes: os.open(O_CREAT|O_EXCL) lets
# exactly one claimer win the race, so two instances can no longer double-alert.
NOTIFY_MARK_DIR = DATA_DIR / ".notified"

def claim_block_alert(day: str, block_id: str, start_min: int) -> bool:
    """Atomically claim the right to alert for (day, block, start). Returns True for
    the first claimer across ALL processes, False if it was already claimed. Fails
    OPEN (True) on any filesystem error so a broken FS can't silence reminders."""
    try:
        NOTIFY_MARK_DIR.mkdir(parents=True, exist_ok=True)
        path = NOTIFY_MARK_DIR / f"{day}__{block_id}__{start_min}"
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return True

def purge_old_alert_marks(keep_day: str) -> None:
    """Drop alert markers from days other than `keep_day` so the dir can't grow."""
    try:
        for p in NOTIFY_MARK_DIR.glob("*"):
            if not p.name.startswith(keep_day + "__"):
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        pass

# ── Settings (persisted to ~/.daily-scheduler/settings.json) ────────────────
# Replaces the old behaviour where model / notify / DND reset to defaults every
# launch (only "Start with Windows" survived, via its Startup-folder .lnk).
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_SETTINGS = {
    "theme":            DEFAULT_THEME,
    "model":            DEFAULT_MODEL,
    "notify_on":        True,
    "notify_lead_min":  0,        # alert this many minutes before a block starts (0 = at start)
    "notify_end_chime": False,    # off by default — start alerts only
    "notify_sound":     True,     # play a tone with alerts (visual still shows)
    "notify_tone":      "chime",  # chime | soft | bright | low | glass
    "notify_volume":    80,       # 0–100
    "dnd_override":     True,
    "plan_day_start":   "08:00",  # default waking window the planner schedules within
    "plan_day_end":     "22:00",
    "ollama_autostart": False,    # keep Ollama off at launch unless the user opts in
    "ollama_models_dir": "",      # empty = Ollama default (~/.ollama/models); used when app starts Ollama
    "update_check_on":  True,     # check GitHub for a newer release on launch + daily
    "calendar_ids":     "primary",  # v4.0: comma-separated Google calendar IDs
    "body_split":       [],       # [calendar_px, sidebar_px, ai_px] — empty = defaults
    "sidebar_split":    [],       # [add_activity_px, summary_px] — empty = defaults
    "ai_panel_w":       340,      # remembered AI width when the panel is open
    "temperature":      0.3,
    "num_ctx":          16384,
    # Optional buffer: at Windows sign-in, wait this many seconds before building the
    # window. The real boot failures are fixed at the source (the `ollama list` hang in
    # list_ollama_models, and the AMD GPU-crash-at-boot via disabling Fast Startup), so
    # this is now just a small settle buffer; raise it if a boot-time GPU reset ever
    # recurs, or set 0 to open the window immediately. Only applies to --startup.
    "startup_delay_sec": 5,
}

def load_settings() -> Dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        if isinstance(data, dict):
            s.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    except Exception:
        pass
    return s

def save_settings(s: Dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2))
    except Exception:
        pass

def parse_hhmm(s: str) -> int:
    """'18:30' / '6:30 pm' / '6 pm' / '24:00' → minutes from midnight.
    End-of-day is 1440 (DAY_END). QTime / strptime reject hour=24, so we accept
    the string form for AI tools and typed times. Raises ValueError on garbage."""
    s = (s or "").strip().lower().replace(".", "")
    if s in ("24:00", "24:0", "24", "2400"):
        return DAY_END
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%H"):
        try:
            t = datetime.strptime(s, fmt)
            return t.hour * 60 + t.minute
        except ValueError:
            continue
    raise ValueError(f"can't parse time '{s}' — use 24h HH:MM (or 24:00 for end of day)")

def coerce_end_min(sm: int, em: int) -> int:
    """Map end-of-day conventions onto DAY_END (1440).
    QTime only holds 00:00–23:59, so End=00:00 with Start later the same day means
    through midnight (e.g. sleep 22:00–24:00). Start=End=00:00 stays zero-length."""
    if em == 0 and sm > 0:
        return DAY_END
    return em


_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}

def resolve_date(s, base: date) -> Optional[str]:
    """Resolve a date the model/user gave (relative to the viewed day `base`) to an
    ISO string. Accepts ISO, Month/Day ('6/14'), M/D/Y, today/tomorrow/yesterday,
    weekday names, or empty (=base). Returns None if it can't be understood.
    Keeps date math OUT of the model — it just passes through what the user said."""
    if s is None:
        return base.isoformat()
    t = str(s).strip().lower()
    if t in ("", "today", "viewed day", "the viewed day", "current day"):
        return base.isoformat()
    if t == "tomorrow":
        return (base + timedelta(days=1)).isoformat()
    if t == "yesterday":
        return (base - timedelta(days=1)).isoformat()
    if t in _WEEKDAYS:                                   # next occurrence after base
        delta = (_WEEKDAYS[t] - base.weekday()) % 7 or 7
        return (base + timedelta(days=delta)).isoformat()
    # Pull out month/day from any numeric form (M/D, M/D/Y, ISO yyyy-mm-dd) and IGNORE
    # the year — models often hallucinate it (e.g. 2023). This is a near-term planner,
    # so snap the month/day to whichever year puts it closest to the viewed day.
    nums = [int(n) for n in re.findall(r"\d+", t)]
    m = d = None
    if len(nums) >= 3 and nums[0] > 31:                  # ISO: year, month, day
        m, d = nums[1], nums[2]
    elif len(nums) >= 2:                                 # M/D or M/D/Y
        m, d = nums[0], nums[1]
    if m is None or d is None:
        return None
    cands = []
    for y in (base.year - 1, base.year, base.year + 1):
        try:
            cands.append(date(y, m, d))
        except ValueError:
            pass
    if not cands:
        return None
    return min(cands, key=lambda c: abs((c - base).days)).isoformat()

# ── Interval helpers ───────────────────────────────────────────────────────
def _merge(intervals):
    merged = []
    for oc in sorted(intervals, key=lambda x: x[0]):
        if merged and oc[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], oc[1]))
        else:
            merged.append(oc)
    return merged

def _free_slots(occupied_pairs, start=DAY_START, end=DAY_END):
    free, cur = [], start
    for s, e in _merge(occupied_pairs):
        if s > cur:
            free.append((cur, s))
        cur = max(cur, e)
    if cur < end:
        free.append((cur, end))
    return free

def _earliest_fit(occupied, cursor, length):
    """Earliest start >= cursor where a `length`-minute block fits without overlapping any
    `occupied` (s, e) interval, before DAY_END. None if it won't fit. Used by plan_day to
    flow chunked tasks past fixed anchors and meetings."""
    for s, e in _free_slots(occupied, cursor, DAY_END):
        if e - s >= length:
            return s
    return None

def norm_title(s: str) -> str:
    """Normalize a title for fuzzy matching: lowercase, alphanumerics + spaces only
    (strips emoji/punctuation so 'gym' matches '🏋 Gym Session')."""
    return " ".join("".join(ch for ch in str(s).lower()
                            if ch.isalnum() or ch.isspace()).split())


def find_free_placement(day_blocks: List[Dict], want_start: int, dur: int) -> Optional[int]:
    """Start time closest to want_start where a dur-minute block fits without
    overlapping anything. None if no gap that size exists in the day."""
    occ  = _merge([(b["startMin"], b["endMin"]) for b in day_blocks])
    best = None
    for s, e in _free_slots(occ):
        if e - s < dur:
            continue
        cand  = min(max(want_start, s), e - dur)
        score = abs(cand - want_start)
        if best is None or score < best[0]:
            best = (score, cand)
    return None if best is None else best[1]


def sequentialize(blocks: List[Dict], blocked=None) -> tuple:
    """Sort by start time and push overlapping blocks later until the plan is
    conflict-free. If `blocked` intervals are given (e.g. read-only calendar events),
    editable blocks are also pushed out of those windows so they never land on a
    meeting. Gaps are preserved; blocks pushed past the end of day are dropped.
    Returns (kept_blocks, n_adjusted, n_dropped)."""
    blocked = sorted(blocked or [])
    out, adjusted, dropped = [], 0, 0
    cur = DAY_START
    for b in sorted(blocks, key=lambda x: (x["startMin"], x["endMin"])):
        dur = b["endMin"] - b["startMin"]
        ns  = max(b["startMin"], cur)
        # Step past any calendar window this block would overlap; moving past one
        # window can push it into the next, so repeat until it sits in the clear.
        moved = True
        while moved:
            moved = False
            for bs, be in blocked:
                if ns < be and ns + dur > bs:
                    ns = be; moved = True
        if ns + dur > DAY_END:
            dropped += 1
            continue
        if ns != b["startMin"]:
            adjusted += 1
        out.append({**b, "startMin": ns, "endMin": ns + dur})
        cur = ns + dur
    return out, adjusted, dropped

def assign_overlap_cols(blocks: List[Dict]) -> List[Dict]:
    """Greedy column assignment for time-overlapping blocks (side-by-side layout).
    Returns copies with `_col` (column index) and `_tcols` (total columns among the
    blocks it overlaps). Input must be sorted by startMin. Shared by the Day
    timeline and the Week view so overlapping blocks render identically."""
    col_ends, result = [], []
    for blk in blocks:
        col = next((i for i, e in enumerate(col_ends) if e <= blk["startMin"]), len(col_ends))
        if col == len(col_ends):
            col_ends.append(0)
        col_ends[col] = blk["endMin"]
        result.append({**blk, "_col": col})
    for i, blk in enumerate(result):
        cols = [blk["_col"]] + [
            b["_col"] for j, b in enumerate(result)
            if j != i and b["startMin"] < blk["endMin"] and b["endMin"] > blk["startMin"]
        ]
        result[i]["_tcols"] = max(cols) + 1
    return result

# ── Google Calendar threads ────────────────────────────────────────────────
class GoogleAuthThread(QThread):
    done  = Signal(object)  # credentials
    error = Signal(str)

    def run(self):
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request

            SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
            creds = None
            if TOKEN_FILE.exists():
                try:
                    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
                except Exception:
                    pass

            if creds and creds.valid:
                self.done.emit(creds); return

            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    TOKEN_FILE.write_text(creds.to_json())
                    self.done.emit(creds); return
                except Exception:
                    pass

            if not CREDS_FILE.exists():
                self.error.emit(
                    "credentials.json not found.\n\n"
                    "Download it from Google Cloud Console:\n"
                    "APIs & Services → Credentials → OAuth 2.0 Client ID\n"
                    "(choose Desktop application) → Download JSON\n"
                    "then load it from the setup screen."
                ); return

            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
            TOKEN_FILE.write_text(creds.to_json())
            self.done.emit(creds)
        except ImportError:
            self.error.emit(
                "Google libraries not installed.\n"
                "Run:  pip install google-auth-oauthlib google-api-python-client"
            )
        except Exception as ex:
            self.error.emit(str(ex))


class CalFetchThread(QThread):
    done  = Signal(dict)   # {iso_date: [events]}
    error = Signal(str)

    def __init__(self, creds, start: date, end: date, calendar_ids: Optional[List[str]] = None):
        super().__init__()
        self.creds  = creds
        self._start = start     # NB: not 'self.start' — that is QThread.start()
        self._end   = end       # exclusive
        self._cals  = parse_calendar_ids(
            ",".join(calendar_ids) if calendar_ids else "primary")

    def run(self):
        try:
            from googleapiclient.discovery import build
            svc = build("calendar", "v3", credentials=self.creds)
            t0  = datetime.combine(self._start, datetime.min.time()).astimezone()
            t1  = datetime.combine(self._end,   datetime.min.time()).astimezone()
            by_date: Dict[str, List[Dict]] = {}
            for cal_id in self._cals:
                page = None
                while True:
                    res = svc.events().list(
                        calendarId=cal_id,
                        timeMin=t0.isoformat(), timeMax=t1.isoformat(),
                        singleEvents=True, orderBy="startTime",
                        maxResults=2500, pageToken=page,
                    ).execute()
                    for ev in res.get("items", []):
                        for entry in normalize_google_event(ev):
                            # Namespace id by calendar so two cals can't collide
                            entry = dict(entry)
                            entry["id"] = f"{cal_id}:{entry['id']}"
                            entry["calendarId"] = cal_id
                            by_date.setdefault(entry["date"], []).append(entry)
                    page = res.get("nextPageToken")
                    if not page:
                        break
            self.done.emit(by_date)
        except Exception as ex:
            self.error.emit(str(ex))

# ── Ollama shutdown ────────────────────────────────────────────────────────
def stop_ollama():
    """Fully stop local Ollama: the tray app, the server, AND the model-runner child
    (llama-server) that actually holds the VRAM. Killing only ollama.exe orphans the
    runner and leaks GPU memory, so the runner images are killed explicitly.
    Returns (ok, message)."""
    try:
        if platform.system() == "Windows":
            NO_WIN = 0x08000000  # CREATE_NO_WINDOW — no console flash
            killed = False
            # Coordinator first, then the runner(s) that pin VRAM. /T also takes any
            # still-attached children. Runner is named "llama-server.exe" on current
            # Ollama; older builds used "ollama_llama_server.exe".
            for image in ("ollama app.exe", "ollama.exe",
                          "llama-server.exe", "ollama_llama_server.exe"):
                r = subprocess.run(
                    ["taskkill", "/F", "/T", "/IM", image],
                    capture_output=True, text=True, creationflags=NO_WIN,
                )
                if "SUCCESS" in (r.stdout or ""):
                    killed = True
            return (True, "Ollama stopped.") if killed else (False, "Ollama wasn't running.")
        else:
            a = subprocess.run(["pkill", "-f", "ollama"], capture_output=True, text=True)
            subprocess.run(["pkill", "-f", "llama-server"], capture_output=True, text=True)
            return (a.returncode == 0,
                    "Ollama stopped." if a.returncode == 0 else "Ollama wasn't running.")
    except Exception as ex:
        return False, str(ex)


def default_ollama_models_dir() -> Path:
    """Ollama's usual models root when OLLAMA_MODELS is unset."""
    # OLLAMA_MODELS overrides; else models live under OLLAMA_HOME or ~/.ollama
    env = (os.environ.get("OLLAMA_MODELS") or "").strip()
    if env:
        return Path(env).expanduser()
    home = (os.environ.get("OLLAMA_HOME") or "").strip()
    base = Path(home).expanduser() if home else (Path.home() / ".ollama")
    return base / "models"

def resolve_ollama_models_dir(settings: Optional[Dict] = None) -> Path:
    """Configured models folder, or Ollama's default path."""
    raw = ""
    if settings is not None:
        raw = str(settings.get("ollama_models_dir") or "").strip()
    if not raw:
        try:
            raw = str(load_settings().get("ollama_models_dir") or "").strip()
        except Exception:
            raw = ""
    if raw:
        return Path(raw).expanduser()
    return default_ollama_models_dir()

def ollama_env(settings: Optional[Dict] = None) -> Dict[str, str]:
    """Environment for `ollama serve` / pull: optional OLLAMA_MODELS override."""
    env = os.environ.copy()
    raw = ""
    if settings is not None:
        raw = str(settings.get("ollama_models_dir") or "").strip()
    else:
        try:
            raw = str(load_settings().get("ollama_models_dir") or "").strip()
        except Exception:
            pass
    if raw:
        p = Path(raw).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        env["OLLAMA_MODELS"] = str(p)
    return env

def start_ollama(settings: Optional[Dict] = None):
    """Launch the local Ollama server (detached). Returns (ok, message).
    If settings include ollama_models_dir, set OLLAMA_MODELS so pulls land there.
    Only applies when *this app* starts the server — a tray/service Ollama already
    running keeps its own path until restarted."""
    try:
        env = ollama_env(settings)
        if platform.system() == "Windows":
            DETACHED = 0x00000008  # DETACHED_PROCESS
            NO_WIN   = 0x08000000  # CREATE_NO_WINDOW
            subprocess.Popen(
                ["ollama", "serve"],
                env=env,
                creationflags=DETACHED | NO_WIN,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        msg = "Starting Ollama…"
        mid = str((settings or {}).get("ollama_models_dir") or "").strip()
        if mid:
            msg += f"\nModels → {Path(mid).expanduser()}"
        return True, msg
    except FileNotFoundError:
        return False, "Ollama not found on PATH.\nInstall it from https://ollama.com/download"
    except Exception as ex:
        return False, str(ex)


# ── Run-at-login ─────────────────────────────────────────────────────────────
# Windows: a .lnk in the user's Startup folder — the most visible/reliable method; it
# shows in Task Manager > Startup and Settings, and the user can see the file directly.
# (The old HKCU Run-key method worked at boot but Task Manager was slow to display it.)
# Linux: an XDG autostart entry (~/.config/autostart/daily-scheduler.desktop) — the
# freedesktop standard, honoured by KDE/GNOME and visible in their autostart settings.
_RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "DailyScheduler"

def _startup_lnk() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (Path(base) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup" / "Daily Scheduler.lnk")

def _startup_target():
    """(target, arguments, working_dir) the shortcut should launch — the APP only,
    never Ollama, with --startup so it opens quietly into the tray."""
    if getattr(sys, "frozen", False):                  # packaged .exe
        return sys.executable, "--startup", str(Path(sys.executable).parent)
    script = Path(__file__).resolve()                  # running from source
    return sys.executable, f'"{script}" --startup', str(script.parent)

def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"

def _remove_legacy_run_key():
    """Drop the old HKCU Run entry so we don't launch twice after migrating."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _RUN_NAME)
    except OSError:
        pass

def _autostart_desktop() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "daily-scheduler.desktop"

def _desktop_exec_line() -> str:
    """Exec= value for the autostart entry. Desktop-entry spec: arguments with spaces
    must be double-quoted, and a literal `"` or `\\` inside them backslash-escaped."""
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    target, args, _ = _startup_target()
    if getattr(sys, "frozen", False):
        return f"{q(target)} {args}"
    script = Path(__file__).resolve()
    return f"{q(target)} {q(str(script))} --startup"

def _set_startup_linux(enabled: bool) -> bool:
    entry = _autostart_desktop()
    if not enabled:
        entry.unlink(missing_ok=True)
        return not entry.exists()
    _, _, workdir = _startup_target()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Daily Scheduler\n"
        "Comment=Daily planner with a local AI assistant\n"
        f"Exec={_desktop_exec_line()}\n"
        f"Path={workdir}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    return entry.exists()

def is_startup_enabled() -> bool:
    if platform.system() == "Windows":
        return _startup_lnk().exists()
    if platform.system() == "Linux":
        return _autostart_desktop().exists()
    return False

def set_startup(enabled: bool) -> bool:
    """Create/remove the run-at-login entry. No admin rights needed."""
    if platform.system() == "Linux":
        try:
            return _set_startup_linux(enabled)
        except Exception:
            return False
    if platform.system() != "Windows":
        return False
    try:
        lnk = _startup_lnk()
        if enabled:
            target, args, workdir = _startup_target()
            lnk.parent.mkdir(parents=True, exist_ok=True)
            ps = (
                "$ws = New-Object -ComObject WScript.Shell; "
                f"$s = $ws.CreateShortcut({_ps_quote(str(lnk))}); "
                f"$s.TargetPath = {_ps_quote(target)}; "
                f"$s.Arguments = {_ps_quote(args)}; "
                f"$s.WorkingDirectory = {_ps_quote(workdir)}; "
                "$s.Description = 'Daily Scheduler'; $s.Save()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, creationflags=0x08000000,
            )
            ok = lnk.exists()
        else:
            if lnk.exists():
                lnk.unlink()
            ok = not lnk.exists()
        _remove_legacy_run_key()       # migrate away from the old Run-key method
        return ok
    except Exception:
        return False


# Built-in alert tones (id → label). Synthesized to ~/.daily-scheduler/tones/.
NOTIFY_TONES = [
    ("chime",  "Chime (default)"),
    ("soft",   "Soft ping"),
    ("bright", "Bright ping"),
    ("low",    "Low thump"),
    ("glass",  "Glass tap"),
]

def _synth_tone_wav(path: Path, tone_id: str) -> bool:
    """Write a short mono WAV for `tone_id`. Returns True on success."""
    try:
        import wave, math, struct
        rate = 44100
        frames = bytearray()

        def blip(freq, secs, vol=0.45):
            n = max(1, int(rate * secs))
            for i in range(n):
                # Attack / decay envelope so samples never click
                env = min(1.0, i / 280.0, (n - i) / 900.0)
                if freq <= 0:
                    sample = 0.0
                else:
                    t = i / rate
                    # Slight 2nd harmonic for a less sterile beep
                    sample = math.sin(2 * math.pi * freq * t)
                    sample += 0.18 * math.sin(4 * math.pi * freq * t)
                frames.extend(struct.pack("<h", int(32767 * vol * env * sample)))

        tid = (tone_id or "chime").lower()
        if tid == "soft":
            blip(520, 0.12, 0.35); blip(0, 0.04); blip(620, 0.18, 0.28)
        elif tid == "bright":
            blip(880, 0.08, 0.4); blip(0, 0.03); blip(1175, 0.1, 0.38); blip(0, 0.02); blip(1397, 0.14, 0.32)
        elif tid == "low":
            blip(180, 0.22, 0.55); blip(0, 0.04); blip(140, 0.28, 0.4)
        elif tid == "glass":
            blip(1480, 0.06, 0.32); blip(0, 0.02); blip(1760, 0.2, 0.22)
        else:  # chime
            blip(660, 0.14, 0.45); blip(0, 0.06); blip(880, 0.22, 0.4)

        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(bytes(frames))
        return True
    except Exception:
        return False

def ensure_alert_wav(tone_id: str = "chime") -> Optional[Path]:
    """Return a reusable WAV path for the given tone (stdlib synthesis)."""
    tid = (tone_id or "chime").lower()
    if tid not in {t[0] for t in NOTIFY_TONES}:
        tid = "chime"
    try:
        p = DATA_DIR / "tones" / f"alert_{tid}.wav"
        # Re-synth if missing or empty (allows tone set to grow without stale files)
        if not p.exists() or p.stat().st_size < 64:
            if not _synth_tone_wav(p, tid):
                return None
        return p
    except Exception:
        return None

# Back-compat alias used by older call sites / tests
def _ensure_alert_wav() -> Optional[Path]:
    return ensure_alert_wav("chime")

def play_alert_sound(parent=None, *, tone: str = "chime", volume: float = 0.8) -> None:
    """Play a short alert tone. `volume` is 0..1. Uses Qt multimedia when available;
    falls back to Windows MessageBeep or QApplication.beep()."""
    vol = max(0.0, min(1.0, float(volume)))
    if vol <= 0.001:
        return
    # Prefer synthesized WAV so the chosen tone is audible on all platforms
    try:
        wav = ensure_alert_wav(tone)
        if wav:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            # Reuse one QSoundEffect on the parent when possible so rapid previews
            # don't pile up player objects.
            fx = None
            if parent is not None:
                fx = getattr(parent, "_alert_fx", None)
            if fx is None:
                fx = QSoundEffect(parent)
                if parent is not None:
                    parent._alert_fx = fx
            path = str(wav)
            if getattr(fx, "_tone_path", None) != path:
                fx.setSource(QUrl.fromLocalFile(path))
                fx._tone_path = path  # type: ignore[attr-defined]
            fx.setVolume(vol)
            fx.play()
            return
    except Exception:
        pass
    try:
        if platform.system() == "Windows":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
    except Exception:
        pass
    try:
        QApplication.beep()
    except Exception:
        pass


def list_ollama_models() -> List[str]:
    """Installed model tags via the Ollama HTTP API (best-effort; [] on any failure).
    Used to populate the model picker alongside the curated RECOMMENDED_MODELS.

    Uses GET /api/tags, NOT the `ollama list` CLI. At Windows sign-in the CLI spawns a
    child (the server/runner) that inherits this process's stdout pipe, so subprocess
    cleanup blocks in the pipe reader thread until that inherited handle closes — which
    it never does — hanging indefinitely and defeating the timeout. That hang ran inside
    AIPanel construction, so MainWindow.__init__ never finished and the app launched with
    NO WINDOW (process alive in Task Manager). The HTTP call spawns no subprocess, fails
    fast (connection refused) when the server is down, and is hard-bounded by `timeout`."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        if not r.ok:
            return []
        return [m["name"] for m in r.json().get("models", []) if m.get("name")]
    except Exception:
        return []

def _model_tag_key(tag: str) -> str:
    """Normalize for install checks: 'qwen3:14b' and 'qwen3:14b:latest' match."""
    t = (tag or "").strip().lower()
    if t.endswith(":latest"):
        t = t[:-7]
    return t

def model_is_installed(tag: str, installed: Optional[List[str]] = None) -> bool:
    """True if `tag` appears in the local Ollama library (best-effort)."""
    if not tag or not str(tag).strip():
        return False
    have = installed if installed is not None else list_ollama_models()
    want = _model_tag_key(tag)
    keys = {_model_tag_key(m) for m in have}
    if want in keys:
        return True
    # Installed name may carry a quant/digest suffix after the curated tag
    for k in keys:
        if k.startswith(want + "-") or k.startswith(want + ":"):
            return True
    return False

class OllamaPullThread(QThread):
    """Stream POST /api/pull for one model tag. Progress is a short status string."""
    progress = Signal(str)
    finished_ok = Signal(str)   # model tag
    failed = Signal(str)

    def __init__(self, model: str, parent=None):
        super().__init__(parent)
        self.model = (model or "").strip()
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if not self.model:
            self.failed.emit("No model name."); return
        try:
            self.progress.emit(f"Pulling {self.model}…")
            # Long read timeout: large models take many minutes; Stop cancels via _stop.
            resp = requests.post(
                f"{OLLAMA_URL}/api/pull",
                json={"name": self.model, "stream": True},
                stream=True, timeout=(10, 3600),
            )
            if resp.status_code == 404:
                self.failed.emit(f"Model '{self.model}' not found on the Ollama library.")
                return
            resp.raise_for_status()
            last = ""
            for line in resp.iter_lines():
                if self._stop:
                    self.failed.emit("Pull cancelled."); return
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                st = data.get("status") or ""
                completed = data.get("completed")
                total = data.get("total")
                if total and completed is not None and total > 0:
                    pct = min(100, int(100 * completed / total))
                    mb_c = completed / (1024 * 1024)
                    mb_t = total / (1024 * 1024)
                    msg = f"{st or 'downloading'}  {pct}%  ({mb_c:.0f}/{mb_t:.0f} MB)"
                else:
                    msg = st or "working…"
                if msg != last:
                    self.progress.emit(msg); last = msg
                if data.get("error"):
                    self.failed.emit(str(data["error"])); return
            self.finished_ok.emit(self.model)
        except requests.exceptions.ConnectionError:
            self.failed.emit("Can't reach Ollama. Press ▶ to start it, then try again.")
        except Exception as ex:
            self.failed.emit(str(ex))


def strip_think(s: str) -> str:
    """Remove reasoning-model chain-of-thought (<think>…</think>) from streamed
    content. Drops complete blocks and any still-open trailing block, so DeepSeek-R1
    style models don't dump their reasoning into the chat."""
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S)
    i = s.find("<think>")
    return s[:i] if i != -1 else s


# ── AI chat transcript (v3.8.0) ────────────────────────────────────────────
# Survives OOM / process kill so a conversation isn't lost mid-stream. Local
# only under DATA_DIR — never log contents (may include schedule talk).
_chat_save_last = 0.0

def _default_chat_histories() -> Dict[str, List[Dict]]:
    return {
        "chat": [{"role": "assistant", "content": AI_GREETING}],
        "plan": [],
        "suggest": [],
    }

def load_chat_histories() -> Dict[str, List[Dict]]:
    """Best-effort restore of the AI panel transcript. Falls back to a fresh
    greeting on any error / missing / corrupt file."""
    out = _default_chat_histories()
    try:
        if not CHAT_FILE.exists():
            return out
        raw = json.loads(CHAT_FILE.read_text(encoding="utf-8"))
        modes = raw.get("modes") if isinstance(raw, dict) else None
        if not isinstance(modes, dict):
            return out
        for key in ("chat", "plan", "suggest"):
            msgs = modes.get(key)
            if not isinstance(msgs, list):
                continue
            clean = []
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant", "tool_note", "error") and isinstance(content, str):
                    clean.append({"role": role, "content": content})
            if clean:
                out[key] = clean
    except Exception:
        pass
    return out

def save_chat_histories(history: Dict[str, List[Dict]], *, force: bool = False) -> bool:
    """Write the in-memory AI histories to CHAT_FILE. Throttled unless force=True
    (turn boundaries / user send always force). Never raises."""
    global _chat_save_last
    try:
        now = time.monotonic()
        if not force and (now - _chat_save_last) < CHAT_SAVE_MIN_SEC:
            return False
        _chat_save_last = now
        payload = {
            "version": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "app_version": APP_VERSION,
            "modes": {
                key: [{"role": m.get("role"), "content": m.get("content", "")}
                      for m in (history.get(key) or [])
                      if isinstance(m, dict) and m.get("role") in
                      ("user", "assistant", "tool_note", "error")]
                for key in ("chat", "plan", "suggest")
            },
        }
        CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CHAT_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CHAT_FILE)
        return True
    except Exception:
        return False


# ── Memory preflight / friendly OOM text (v3.8.0) ──────────────────────────
def free_ram_gb() -> Optional[float]:
    """Best-effort free/available system RAM in GiB, or None if unknown.
    This is NOT free VRAM — only a rough signal for soft warnings."""
    try:
        if platform.system() == "Linux":
            avail_kb = None
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        avail_kb = int(line.split()[1]); break
                    if line.startswith("MemFree:") and avail_kb is None:
                        avail_kb = int(line.split()[1])
            if avail_kb is not None:
                return avail_kb / (1024 * 1024)
        elif platform.system() == "Windows":
            import ctypes

            class _MEM(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = _MEM()
            st.dwLength = ctypes.sizeof(_MEM)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullAvailPhys / (1024 ** 3)
    except Exception:
        pass
    return None

def model_need_gb(model: str) -> Optional[float]:
    """Rough GB need from MODEL_PROFILES.vram (takes the highest number in the
    string, e.g. '~13–14 GB' → 14). None if unlisted / unparseable."""
    p = model_profile(model)
    if not p:
        return None
    nums = re.findall(r"(\d+(?:\.\d+)?)", p.get("vram") or "")
    if not nums:
        return None
    return max(float(n) for n in nums)

def memory_warning_for(model: str) -> str:
    """Soft preflight blurb, or '' if no concern / unknown. Never hard-blocks."""
    need = model_need_gb(model)
    free = free_ram_gb()
    if need is None or free is None:
        return ""
    # Only warn when free system RAM is clearly under the model's typical footprint.
    # (Free RAM ≠ free VRAM — wording makes that explicit.)
    if free >= need * 0.9:
        return ""
    return (
        f"Heads-up: about {free:.0f} GB system RAM free, and '{model}' typically wants "
        f"~{need:.0f} GB of GPU memory. Free RAM is not the same as free VRAM — if the "
        f"GPU is busy (games, browser, another model), the model may get killed mid-reply. "
        f"Close heavy apps or switch to a smaller model (e.g. qwen3:14b)."
    )

def friendly_stream_error(exc: BaseException, *, got_tokens: bool, model: str) -> str:
    """Human text for mid-stream / connection failures (OOM, server death, etc.)."""
    name = type(exc).__name__
    low = f"{name}: {exc}".lower()
    oomish = any(k in low for k in (
        "connection", "reset", "broken pipe", "chunked", "remote", "aborted",
        "eof", "protocol", "forcibly closed", "remotedisconnected",
    ))
    if got_tokens or oomish:
        return (
            f"The reply was cut off mid-stream"
            f"{' after the model started answering' if got_tokens else ''}.\n\n"
            f"Most often the model process was killed for memory (OOM) or Ollama "
            f"restarted. Try:\n"
            f"  • Unload (⏏) and send again\n"
            f"  • A smaller model (qwen3:14b is the roomy daily driver)\n"
            f"  • Close other GPU apps, then ▶ start Ollama again\n\n"
            f"(Model was '{model}'. Technical: {name})"
        )
    return (
        "Can't reach Ollama. Click the ▶ button to start it,\n"
        "or run 'ollama serve' in a terminal."
    )


def unload_ollama_model(model):
    """Unload a model from memory but keep the Ollama server running.
    Uses keep_alive=0, the documented way to free VRAM/RAM immediately."""
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": 0}, timeout=10,
        )
        if r.ok:
            return True, f"Unloaded '{model}' from memory."
        return False, f"Ollama returned status {r.status_code}."
    except requests.exceptions.ConnectionError:
        return False, "Ollama isn't running."
    except Exception as ex:
        return False, str(ex)


# ── Ollama streaming thread ────────────────────────────────────────────────
class OllamaCheckThread(QThread):
    result = Signal(bool)
    def run(self):
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            self.result.emit(r.ok)
        except Exception:
            self.result.emit(False)


class UpdateCheckThread(QThread):
    """Ask GitHub, once, whether a newer release exists. Fails SILENTLY on ANY
    error — offline, timeout, 404 (repo still private / no releases yet), or a
    403 rate-limit — because an update check must never interrupt the planner.
    Emits update_available ONLY when the latest published tag is strictly newer
    than APP_VERSION. GitHub requires a User-Agent or it returns 403."""
    update_available = Signal(str, str)   # tag_name, html_url

    def run(self):
        try:
            r = requests.get(
                LATEST_RELEASE_API, timeout=(5, 8),
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": f"DailyScheduler/{APP_VERSION}"},
            )
            if r.status_code != 200:      # 404 while private, 403 rate-limited, …
                return
            data = r.json()
            tag = str(data.get("tag_name", "")).strip()
            url = str(data.get("html_url") or RELEASES_PAGE)
            if is_newer_version(tag, APP_VERSION):
                self.update_available.emit(tag, url)
        except Exception:
            pass


class OllamaThread(QThread):
    token      = Signal(str)
    done       = Signal()
    tool_calls = Signal(list)
    error      = Signal(str)

    def __init__(self, messages, model, tools=None, num_ctx=16384, temperature=0.3):
        super().__init__()
        self.messages    = messages
        self.model       = model
        self.tools       = tools
        self.num_ctx     = num_ctx
        self.temperature = temperature
        self._stop       = False

    def stop(self): self._stop = True

    def run(self):
        got_tokens = False
        try:
            payload = {"model": self.model, "messages": self.messages, "stream": True,
                       "options": {"num_ctx": self.num_ctx,
                                   "temperature": self.temperature, "top_p": 0.9}}
            if self.tools:
                payload["tools"] = self.tools
            # (connect, read) timeouts: fail fast when the server is down, but the
            # read timeout is the max SILENCE before the first streamed byte — and
            # Ollama sends nothing while it loads a model into VRAM, so a cold load
            # of a 24B model can far exceed 120 s. 600 s covers a big cold load;
            # the Stop button still works between chunks once streaming starts.
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat", json=payload,
                stream=True, timeout=(5, 600),
            )
            # 404 here almost always means "model not installed" — translate it.
            if resp.status_code == 404:
                err = ""
                try:
                    err = resp.json().get("error", "")
                except Exception:
                    pass
                self.error.emit(
                    f"Model '{self.model}' isn't installed.\n\n"
                    f"Pull it from a terminal:\n    ollama pull {self.model}\n\n"
                    f"Or type a model you already have into the Model field above."
                    + (f"\n\n(Ollama said: {err})" if err else "")
                )
                return
            resp.raise_for_status()
            calls = []
            raw, sent = "", 0          # raw = full content; sent = chars already emitted
            for line in resp.iter_lines():
                if self._stop: break
                if not line: continue
                try:
                    data = json.loads(line)
                    msg  = data.get("message") or {}
                    c    = msg.get("content", "")
                    if c:                       # strip <think> reasoning, emit only the delta
                        raw += c
                        vis = strip_think(raw)
                        if len(vis) > sent:
                            self.token.emit(vis[sent:]); sent = len(vis)
                            got_tokens = True
                    if msg.get("tool_calls"):
                        calls.extend(msg["tool_calls"])
                    if data.get("done"): break
                except Exception:
                    pass
            if calls and not self._stop:
                self.tool_calls.emit(calls)
            else:
                self.done.emit()
        except requests.exceptions.Timeout:
            self.error.emit(
                f"Ollama didn't respond in time. If '{self.model}' was cold-loading "
                f"into VRAM it may be ready now — try sending your message again. "
                f"A smaller model also loads (and answers) faster.")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ContentDecodingError,
                BrokenPipeError, ConnectionResetError) as ex:
            self.error.emit(friendly_stream_error(ex, got_tokens=got_tokens, model=self.model))
        except Exception as ex:
            # Mid-stream death often surfaces as a generic RequestException /
            # ProtocolError once the runner is OOM-killed.
            if got_tokens:
                self.error.emit(friendly_stream_error(ex, got_tokens=True, model=self.model))
            else:
                self.error.emit(str(ex))

# ── AI tools — let the model edit the schedule directly ────────────────────
AI_TOOLS = [
    {"type": "function", "function": {
        "name": "add_block",
        "description": "Add a block to the user's schedule. Times are 24-hour HH:MM.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the currently viewed day."},
            "start": {"type": "string", "description": "Start time, 24h HH:MM"},
            "end":   {"type": "string", "description": "End time, 24h HH:MM"},
            "title": {"type": "string", "description": "Short title for the block"},
            "type":  {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES],
                       "description": "Activity category"},
        }, "required": ["start", "end", "title"]}}},
    {"type": "function", "function": {
        "name": "delete_block",
        "description": "Delete user-created block(s). Identify the block by title and/or by "
                       "its time. To remove ONE specific time slot, pass its start time in "
                       "'at' (e.g. at='14:00' deletes the block starting at 2pm). Combine "
                       "'title' + 'at' to be exact when several blocks share a title.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "title": {"type": "string", "description": "Title (or part of it) of the block to delete."},
            "at":    {"type": "string", "description": "Start time of the specific block to delete, 24h HH:MM (e.g. '14:00'). Targets just that one time slot."},
        }}}},
    {"type": "function", "function": {
        "name": "move_block",
        "description": "Move, resize, or rename ONE user-created block. Identify which block "
                       "with 'title' and/or 'at' (its current start time); use 'at' when "
                       "several blocks share a title. Then set the new time/date/title.",
        "parameters": {"type": "object", "properties": {
            "date":     {"type": "string", "description": "Date the block is currently on (YYYY-MM-DD). Omit for the viewed day."},
            "title":    {"type": "string", "description": "Title (or part) of the block to move."},
            "at":       {"type": "string", "description": "Current start time of the block to move, 24h HH:MM. Use to pick the exact block when titles repeat."},
            "start":    {"type": "string", "description": "NEW start time 24h HH:MM."},
            "end":      {"type": "string", "description": "NEW end time 24h HH:MM."},
            "new_date": {"type": "string", "description": "New date YYYY-MM-DD if moving to another day."},
            "new_title": {"type": "string", "description": "New title, to rename the block."},
        }}}},
    {"type": "function", "function": {
        "name": "list_blocks",
        "description": "List everything on the schedule for a date.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
        }}}},
    {"type": "function", "function": {
        "name": "clear_day",
        "description": "Delete ALL editable blocks on a date (wipe the day's plan) in one call.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
        }}}},
    {"type": "function", "function": {
        "name": "shift_blocks",
        "description": "Shift EVERY editable block on a date by one offset. Use this single call to move a whole day — never move blocks one at a time for this.",
        "parameters": {"type": "object", "properties": {
            "date":    {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "minutes": {"type": "integer", "description": "Offset in minutes. Positive = later, negative = earlier (120 = 2 hours later)."},
            "hours":   {"type": "integer", "description": "Optional whole-hour offset, added to 'minutes' (hours=2 → 120 min later). Use either field."},
        }, "required": ["minutes"]}}},
    {"type": "function", "function": {
        "name": "replace_day",
        "description": "Replace the ENTIRE set of editable blocks on a date with a new plan, in one atomic call. Best way to restructure a day, split work into chunks, or build a plan with breaks. IMPORTANT: this DELETES every existing block not in your list — if the user wants to keep other blocks, include them in 'blocks' too.",
        "parameters": {"type": "object", "properties": {
            "date":   {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "blocks": {"type": "array", "description": "Complete new plan for the day, in time order.",
                "items": {"type": "object", "properties": {
                    "start": {"type": "string", "description": "24h HH:MM"},
                    "end":   {"type": "string", "description": "24h HH:MM"},
                    "title": {"type": "string"},
                    "type":  {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
                }, "required": ["start", "end", "title"]}},
        }, "required": ["blocks"]}}},
    {"type": "function", "function": {
        "name": "copy_day",
        "description": "Copy ALL editable blocks from one date to another in one call. "
                       "Use this for 'copy/duplicate my schedule to <day>'. By default it "
                       "REPLACES the target day's blocks with the copies.",
        "parameters": {"type": "object", "properties": {
            "from_date": {"type": "string", "description": "Source date (omit = viewed day). Pass the user's own words — a weekday name ('Thursday'), 'today', 6/14, or YYYY-MM-DD."},
            "to_date":   {"type": "string", "description": "Target date. Pass the user's own words — a weekday name ('Thursday'), 'tomorrow', 6/14, or YYYY-MM-DD — NOT a date you worked out yourself; the app resolves it."},
            "merge":     {"type": "boolean", "description": "If true, keep the target's existing blocks and add the copies alongside them. Default false (replace)."},
        }, "required": ["to_date"]}}},
    {"type": "function", "function": {
        "name": "add_recurring",
        "description": "Add the SAME block to multiple days in one call — for repeating "
                       "things like classes or a daily study slot. Specify the days either "
                       "with 'weekdays' (e.g. ['monday','wednesday'], or 'weekdays'/'weekends'/"
                       "'daily') optionally over several 'weeks', or with an explicit 'dates' list.",
        "parameters": {"type": "object", "properties": {
            "title":    {"type": "string"},
            "start":    {"type": "string", "description": "24h HH:MM"},
            "end":      {"type": "string", "description": "24h HH:MM"},
            "type":     {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
            "weekdays": {"type": "array", "items": {"type": "string"},
                          "description": "Weekday names and/or 'weekdays','weekends','daily'. Applied across the next 'weeks' starting from the viewed day."},
            "weeks":    {"type": "integer", "description": "How many weeks for weekday recurrence (default 1, max 8)."},
            "dates":    {"type": "array", "items": {"type": "string"},
                          "description": "Explicit list of dates (YYYY-MM-DD, or 6/14, tomorrow…). Use instead of weekdays for specific days."},
        }, "required": ["start", "end", "title"]}}},
    {"type": "function", "function": {
        "name": "clear_range",
        "description": "Delete editable blocks that fall within a time window on a date "
                       "(e.g. 'clear my afternoon' → 12:00–18:00). Use clear_day for the whole day.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "start": {"type": "string", "description": "Window start 24h HH:MM."},
            "end":   {"type": "string", "description": "Window end 24h HH:MM."},
        }, "required": ["start", "end"]}}},
    {"type": "function", "function": {
        "name": "find_free_time",
        "description": "Read-only: list open gaps (free of editable blocks AND calendar "
                       "events) on a date. Use to answer 'when am I free?' and to choose "
                       "where to place new blocks. Does not modify anything.",
        "parameters": {"type": "object", "properties": {
            "date":     {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "duration": {"type": "integer", "description": "Only return gaps at least this many minutes long."},
            "after":    {"type": "string", "description": "Only consider time after this (24h HH:MM)."},
            "before":   {"type": "string", "description": "Only consider time before this (24h HH:MM)."},
        }}}},
    {"type": "function", "function": {
        "name": "split_block",
        "description": "Split one existing block into focused chunks separated by short "
                       "breaks (pomodoro-style), within its original time span. The focus "
                       "chunks keep the block's type; the breaks are downtime (see break_type). "
                       "Identify the block by title and/or 'at' (start time).",
        "parameters": {"type": "object", "properties": {
            "date":   {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "title":  {"type": "string", "description": "Title (or part) of the block to split."},
            "at":     {"type": "string", "description": "Start time of the block to split, 24h HH:MM."},
            "chunk":  {"type": "integer", "description": "Length of each focus chunk in minutes (default 30)."},
            "break":  {"type": "integer", "description": "Length of each break in minutes (default 5; 0 for none)."},
            "break_type": {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES],
                            "description": "Category for the breaks (default 'free' = rest). A break is rest, not study — don't reuse the work block's type."},
        }}}},
    {"type": "function", "function": {
        "name": "schedule_tasks",
        "description": "INTELLIGENT PLANNING — your main tool for 'plan my day' / 'fit these "
                       "things in'. You supply the tasks (with durations, urgency, and "
                       "preferred time of day from your own reasoning); the app places each "
                       "into a real free slot at reasonable hours, around existing blocks and "
                       "calendar events. It NEVER deletes anything and never overlaps, so it's "
                       "safe to plan around meals/classes the user is keeping. Higher-priority "
                       "tasks get earlier slots.",
        "parameters": {"type": "object", "properties": {
            "date":      {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "day_start": {"type": "string", "description": "Earliest time to schedule (24h HH:MM). Defaults to the user's waking-hours start (and not earlier than now when planning today)."},
            "day_end":   {"type": "string", "description": "Latest time to schedule (24h HH:MM, default 22:00)."},
            "tasks": {"type": "array", "description": "Tasks to place, in any order.",
                "items": {"type": "object", "properties": {
                    "title":    {"type": "string"},
                    "minutes":  {"type": "integer", "description": "How long the task needs."},
                    "type":     {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
                    "priority": {"type": "string", "enum": ["high", "normal", "low"],
                                  "description": "Urgent/important → 'high' (placed earliest)."},
                    "prefer":   {"type": "string", "description": "Preferred time: 'morning'/'afternoon'/'evening' or a time like '15:00'. Optional."},
                }, "required": ["title", "minutes"]}},
        }, "required": ["tasks"]}}},
    {"type": "function", "function": {
        "name": "reflow_from_now",
        "description": "\"I'm running late\" — push the blocks still to come on a day later "
                       "(or earlier) by an offset, leaving past/ongoing blocks alone. Use when "
                       "the user has fallen behind and wants the rest of the day shifted.",
        "parameters": {"type": "object", "properties": {
            "date":    {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "minutes": {"type": "integer", "description": "How far to push upcoming blocks. Positive = later (running behind), negative = earlier (ahead)."},
            "from":    {"type": "string", "description": "Only move blocks starting at/after this time (24h HH:MM). Default: the current time when the day is today, else the start of the day."},
        }, "required": ["minutes"]}}},
    {"type": "function", "function": {
        "name": "plan_for_deadline",
        "description": "Spread work for a deadline across the days leading up to it. Give the "
                       "total time the job needs and (optionally) a session length; the app "
                       "places one focus session per day into free time across the days before "
                       "the deadline, never overlapping existing blocks. Use for 'study 4 hours "
                       "before Friday's exam' or 'plan my essay over the week'. Idempotent — "
                       "re-running doesn't duplicate sessions already placed.",
        "parameters": {"type": "object", "properties": {
            "title":    {"type": "string", "description": "What the work is (e.g. 'Study for chem exam')."},
            "deadline": {"type": "string", "description": "Due date YYYY-MM-DD, or words like 'friday' / '6/20'."},
            "minutes":  {"type": "integer", "description": "Total time the whole job needs, in minutes."},
            "session":  {"type": "integer", "description": "Length of each daily focus session in minutes (default 60)."},
            "type":     {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES], "description": "Activity category (default study)."},
            "start_date": {"type": "string", "description": "First day to start from (YYYY-MM-DD; default today)."},
        }, "required": ["title", "deadline", "minutes"]}}},
    {"type": "function", "function": {
        "name": "week_summary",
        "description": "Read-only: total time per category over a date range (default the week "
                       "containing the viewed day), with a per-day average. Use to answer 'how "
                       "much sleep/study/exercise did I get this week?' and to spot balance "
                       "problems. Modifies nothing.",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string", "description": "Range start (YYYY-MM-DD or words). Omit for the start of the viewed week."},
            "end":   {"type": "string", "description": "Range end (YYYY-MM-DD or words). Omit for the end of the viewed week."},
        }}}},
    {"type": "function", "function": {
        "name": "plan_day",
        "description": "Build a whole day by laying out ORDERED tasks around FIXED anchors "
                       "(meals, workout, wake-up) and calendar events — the reliable way to "
                       "handle 'plan my day: X first then Y, lunch at 13:00, workout at 16:00, "
                       "30-min chunks with breaks'. Give each task its TOTAL focus minutes "
                       "(breaks are EXTRA and NOT counted) and optionally a chunk + break size; "
                       "the app places everything in order from 'start', splits each task into "
                       "chunks separated by breaks, and flows the rest PAST every fixed anchor "
                       "and meeting. REPLACES the day's editable blocks, so include every fixed "
                       "item you want kept. PREFER THIS over hand-building with replace_day "
                       "whenever there's a set order + fixed times + chunking.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "start": {"type": "string", "description": "When tasks begin, 24h HH:MM (default the user's waking-hours start; on today, not before now)."},
            "fixed": {"type": "array", "description": "Anchors placed at exact times that tasks flow around (lunch, workout, wake-up).",
                "items": {"type": "object", "properties": {
                    "title":   {"type": "string"},
                    "start":   {"type": "string", "description": "24h HH:MM"},
                    "minutes": {"type": "integer", "description": "Length in minutes (or give 'end')."},
                    "end":     {"type": "string", "description": "24h HH:MM (alternative to minutes)."},
                    "type":    {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
                }, "required": ["title", "start"]}},
            "tasks": {"type": "array", "description": "Tasks in the ORDER to do them.",
                "items": {"type": "object", "properties": {
                    "title":   {"type": "string"},
                    "minutes": {"type": "integer", "description": "TOTAL focus time for this task — do NOT include break time."},
                    "type":    {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
                    "chunk":   {"type": "integer", "description": "Split into chunks this many minutes long (omit = one solid block)."},
                    "break":   {"type": "integer", "description": "Break minutes between chunks (default 15 when chunk is set; 0 for none)."},
                }, "required": ["title", "minutes"]}},
        }, "required": ["tasks"]}}},
    {"type": "function", "function": {
        "name": "make_room",
        "description": "Add a FIXED appointment at an exact time and shuffle the day's existing "
                       "blocks AROUND it — WITHOUT deleting any. THIS is how to handle 'I have a "
                       "meeting 12:00–13:30, adjust my schedule' or 'something came up at 3pm, "
                       "move things around it'. The appointment (plus optional buffer time) and "
                       "any 'pin'ned blocks stay fixed; every OTHER block keeps its order and "
                       "duration and is shifted to flow around them (and around calendar events). "
                       "Use this instead of add_block (which would just drop the appointment in a "
                       "random free slot) or a chain of move_block calls.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "title": {"type": "string", "description": "Name of the appointment (e.g. 'College Applications Meeting')."},
            "start": {"type": "string", "description": "Appointment start, 24h HH:MM."},
            "end":   {"type": "string", "description": "Appointment end, 24h HH:MM."},
            "type":  {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES], "description": "Category (default 'extra')."},
            "buffer_before": {"type": "integer", "description": "Minutes of transition time to reserve right BEFORE the appointment (added as a Break). Default 0."},
            "buffer_after":  {"type": "integer", "description": "Minutes of transition time to reserve right AFTER the appointment. Default 0."},
            "pin": {"type": "array", "items": {"type": "string"},
                     "description": "Titles of existing blocks to keep FIXED in place (e.g. ['Workout/Break']); everything else flows around them. Optional."},
        }, "required": ["title", "start", "end"]}}},
]

AI_TOOL_NAMES = {t["function"]["name"] for t in AI_TOOLS}

# How many tool-call rounds the model may take in one turn. High enough for
# edit → verify (list_blocks) → fix → re-verify cycles, capped to avoid runaway loops.
MAX_TOOL_ROUNDS = 8


def _json_spans(s: str):
    """Yield balanced {...} / [...] substrings (brace-depth aware, handles nesting)."""
    depth, start = 0, None
    for i, ch in enumerate(s):
        if ch in "{[":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "}]" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield s[start:i + 1]
                start = None


def looks_like_tool_text(s: str) -> bool:
    """Heuristic: is this streamed content actually a tool call printed as text?"""
    t = s.lstrip()
    return (t.startswith("{") or t.startswith("[")
            or t.startswith("<|python_tag|>") or t.startswith("```")
            or "<|python_tag|>" in s[:40])


def extract_tool_calls(text: str):
    """Recover tool calls a model printed as content text instead of using the
    native tool_calls channel. Handles <|python_tag|>, ``` fences, bare objects,
    JSON arrays, and {type:function, function:{...}} / {name, arguments|parameters}
    shapes. Returns a list of {"name", "args"} for known tools only."""
    if not text:
        return []
    s = text.replace("<|python_tag|>", " ").replace("<|eom_id|>", " ")
    for fence in ("```json", "```tool_code", "```python", "```tool_call", "```"):
        s = s.replace(fence, " ")
    found = []
    for span in _json_spans(s):
        try:
            obj = json.loads(span)
        except Exception:
            continue
        for it in (obj if isinstance(obj, list) else [obj]):
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("function"), dict):   # {type:function, function:{...}}
                it = it["function"]
            name = it.get("name")
            if name not in AI_TOOL_NAMES:
                continue
            args = it.get("arguments")
            if args is None:
                args = it.get("parameters", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            found.append({"name": name, "args": args if isinstance(args, dict) else {}})
    return found

AI_GREETING = (
    "Hey! I'm your scheduling assistant — I can see your calendar and edit it directly. "
    "Try things like:\n\n"
    "  •  \"Add a study block from 2 to 4pm\"\n"
    "  •  \"Shift everything 2 hours later\"\n"
    "  •  \"Clear out tomorrow\"\n"
    "  •  \"Replan my afternoon: 2h of AP work in 30-min chunks with breaks\"\n\n"
    "What would you like to do with your day?"
)

# ── Per-model prompt tuning ─────────────────────────────────────────────────
# Each local model has different failure modes on this tool-heavy task. The base
# system prompt is the same for all; model_guidance() appends an extensively
# detailed, model-specific addendum that targets that family's known weaknesses.
# Common thread: emit NATIVE tool calls (not prose, not printed JSON), use the
# correct single bulk tool, keep exact argument shapes, and verify with list_blocks.

_R1_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — DeepSeek-R1 (reasoning model) ══\n"
    "Your private chain-of-thought is HIDDEN from the user and is stripped out before "
    "anything is shown. Reasoning therefore changes NOTHING on its own — only tool calls "
    "do. Obey these rules exactly:\n"
    "1. THINK BRIEFLY, THEN ACT. Do a short reasoning pass, then stop and act. Do not loop "
    "or re-derive the whole day repeatedly; long reasoning wastes the context window.\n"
    "2. A TOOL CALL IS MANDATORY for any request to add / move / delete / rename / clear / "
    "shift / copy / split / plan / replace. Writing 'I will add…', 'You could…', or showing "
    "the finished schedule as text DOES NOTHING. If you catch yourself describing the change "
    "in prose, STOP and emit the tool call instead.\n"
    "3. USE THE NATIVE FUNCTION-CALL CHANNEL. Never print the call as text, as a JSON object, "
    "as an array, or inside ``` fences. If — and only if — your runtime truly cannot call "
    "functions, output ONE single JSON object {\"name\":\"<tool>\",\"arguments\":{…}} and "
    "absolutely nothing else (no prose, no fences, no <think> around it).\n"
    "4. EXACT ARGUMENT SHAPES (R1 is the most likely to get these wrong):\n"
    "   • Times are STRINGS in 24-hour zero-padded 'HH:MM' — '09:00', '14:30', not '9', "
    "'9am', or 900.\n"
    "   • Dates are 'YYYY-MM-DD', or pass the user's own words ('6/14', 'tomorrow', "
    "'monday'); NEVER invent or change the year.\n"
    "   • schedule_tasks → 'tasks' is an ARRAY of objects, each at least {\"title\":str, "
    "\"minutes\":int}; optional \"type\", \"priority\" (high/normal/low), \"prefer\".\n"
    "   • replace_day → 'blocks' is an ARRAY of {\"start\",\"end\",\"title\",\"type\"}.\n"
    "5. ONE TOOL CALL PER STEP. After each call, READ the result text that comes back, then "
    "decide the next step. When done editing, call list_blocks ONCE to verify, fix anything "
    "wrong, then write ONE short confirmation sentence.\n"
    "6. NEVER chain many add_block calls for a bulk job — use the single matching bulk tool "
    "(schedule_tasks, replace_day, shift_blocks, clear_day, copy_day, add_recurring).\n"
    "7. If genuinely ambiguous, ask ONE short question. But if the user named a time, target "
    "that block with 'at' = its start time; don't ask.\n"
)

_GPTOSS_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — gpt-oss ══\n"
    "1. ACT, DON'T NARRATE. The moment the user asks for a schedule change, call the matching "
    "tool. Do NOT first write an analysis, a numbered plan, or 'Here's what I'll do' — the "
    "tool call IS the action. Keep all reasoning short and low-effort; this is simple "
    "scheduling, not a puzzle.\n"
    "2. NATIVE TOOL CALLS ONLY. Use the function-calling channel. Never emit the call as "
    "prose, as printed JSON, or inside a code block, and never narrate it in an analysis "
    "channel.\n"
    "3. ONE BEST TOOL PER REQUEST. For whole-day or bulk changes use the bulk tool "
    "(schedule_tasks to plan, replace_day to rebuild, shift_blocks to move everything, "
    "clear_day/clear_range to wipe, copy_day to duplicate, add_recurring to repeat) — never "
    "a sequence of single add_block calls.\n"
    "4. EXACT SHAPES. Times = 24-hour 'HH:MM' strings; dates = 'YYYY-MM-DD' or the user's "
    "words (never invent the year). schedule_tasks.tasks and replace_day.blocks are JSON "
    "arrays of objects with the required keys.\n"
    "5. After multi-step edits, verify ONCE with list_blocks, fix if needed, then confirm in "
    "a single sentence — do not restate the whole schedule.\n"
)

_QWEN3_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — Qwen3 ══\n"
    "/no_think\n"
    "Respond directly, WITHOUT an extended reasoning pass (no <think> block) — this is a "
    "simple scheduling task, so decide fast and call the tool. Your tool-calling is strong; "
    "use it decisively.\n"
    "1. DECIDE QUICKLY. This is a straightforward scheduling assistant; don't enumerate many "
    "alternatives or second-guess. Keep any thinking brief, then call the tool.\n"
    "2. A TOOL CALL IS REQUIRED for every add / move / delete / rename / clear / shift / "
    "copy / split / plan / replace request — never just describe the change in words.\n"
    "3. ONE TOOL FOR BULK JOBS: schedule_tasks to plan, replace_day to rebuild from scratch, "
    "shift_blocks to move the whole day, add_recurring for repeats. Don't chain single "
    "add_block calls.\n"
    "4. EXACT SHAPES. Times = zero-padded 24-hour 'HH:MM' strings; dates = 'YYYY-MM-DD' or "
    "the user's words (never invent the year). schedule_tasks.tasks and replace_day.blocks "
    "are arrays of objects.\n"
    "5. Verify with list_blocks after multi-step edits, fix anything wrong, then confirm in "
    "one short sentence.\n"
)

_QWEN25_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — Qwen2.5 ══\n"
    "1. ALWAYS CALL A TOOL for any schedule change (add / move / delete / rename / clear / "
    "shift / copy / split / plan / replace). Prose alone changes nothing — the calendar only "
    "updates through tool calls.\n"
    "2. NATIVE CHANNEL ONLY. Use the function-calling interface; do not print the call as "
    "text, JSON, an array, or inside ``` fences.\n"
    "3. ONE TOOL PER JOB. For bulk or whole-day work use schedule_tasks / replace_day / "
    "shift_blocks / clear_day / copy_day / add_recurring instead of repeated add_block "
    "calls.\n"
    "4. EXACT SHAPES. Times = 24-hour 'HH:MM' strings; dates = 'YYYY-MM-DD' or the user's "
    "words (never invent the year). schedule_tasks.tasks and replace_day.blocks are arrays "
    "of objects.\n"
    "5. Be concise: after verifying with list_blocks, confirm in one short sentence — don't "
    "restate the whole schedule.\n"
)

_GENERIC_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS ══\n"
    "1. ALWAYS call the matching tool for any schedule change; never only describe it.\n"
    "2. Prefer the native tool-calling channel. If your runtime cannot call functions, emit "
    "ONE single JSON object {\"name\":\"<tool>\",\"arguments\":{…}} and nothing else — no "
    "prose, no code fences.\n"
    "3. Use ONE tool for bulk jobs (schedule_tasks / replace_day / shift_blocks / clear_day / "
    "copy_day / add_recurring); never chain single add_block calls.\n"
    "4. EXACT SHAPES. Times = 24-hour 'HH:MM' strings; dates = 'YYYY-MM-DD' or the user's "
    "words (never invent the year). schedule_tasks.tasks and replace_day.blocks are arrays "
    "of objects.\n"
    "5. Verify with list_blocks after multi-step edits, then confirm in one short sentence.\n"
)

_GEMMA_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — Gemma ══\n"
    "You're a capable general model but less battle-tested at tool-calling than Qwen, so be "
    "disciplined and literal:\n"
    "1. ALWAYS emit a real TOOL CALL for any add / move / delete / rename / clear / shift / "
    "copy / split / plan / replace request. Writing out the change, or showing a finished "
    "schedule as text, does NOTHING — only tool calls edit the calendar.\n"
    "2. USE THE NATIVE FUNCTION-CALL CHANNEL. Never print the call as prose, markdown, or inside "
    "``` fences. If — and only if — you truly cannot call a function, output ONE single JSON "
    "object {\"name\":\"<tool>\",\"arguments\":{…}} and nothing else.\n"
    "3. EXACT ARGUMENT SHAPES (Gemma tends to drift here): times are 24-hour zero-padded "
    "'HH:MM' strings ('09:00', '14:30'); dates are 'YYYY-MM-DD' or the user's own words "
    "('6/14', 'tomorrow') — NEVER invent the year. plan_day.tasks / plan_day.fixed / "
    "schedule_tasks.tasks / replace_day.blocks are JSON ARRAYS of objects with the required keys.\n"
    "4. ONE TOOL PER BULK JOB: plan_day to build an ordered day, schedule_tasks to fit tasks, "
    "replace_day to rebuild, shift_blocks to move the whole day, add_recurring to repeat — never "
    "a chain of single add_block calls.\n"
    "5. Keep replies short. After multi-step edits call list_blocks, fix anything in its "
    "CONFLICTS section, then confirm in ONE sentence.\n"
)

_GLM_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — GLM ══\n"
    "You're a fast agentic model — act decisively and keep internal reasoning brief.\n"
    "1. A TOOL CALL IS REQUIRED for every add / move / delete / rename / clear / shift / copy / "
    "split / plan / replace — never just describe the change in words.\n"
    "2. NATIVE TOOL CALLS only — do not print the call as text, JSON, or inside ``` fences. Any "
    "hidden reasoning is stripped before the user sees it, so it changes nothing on its own; "
    "keep it short — this is simple scheduling, not a puzzle.\n"
    "3. ONE TOOL FOR BULK JOBS: plan_day (ordered day with fixed anchors + chunking), "
    "schedule_tasks (fit tasks into free time), replace_day (rebuild), shift_blocks (move the "
    "whole day). Don't chain single add_block calls.\n"
    "4. EXACT SHAPES: times = 24-hour zero-padded 'HH:MM' strings; dates = 'YYYY-MM-DD' or the "
    "user's words (never invent the year). tasks / fixed / blocks are arrays of objects.\n"
    "5. After multi-step edits, call list_blocks, fix anything in its CONFLICTS section, then "
    "confirm in ONE short sentence.\n"
)

_MISTRAL_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — Mistral ══\n"
    "Your function-calling is solid — use it precisely and literally:\n"
    "1. ALWAYS call the matching tool for any schedule change; prose alone changes nothing.\n"
    "2. Use the NATIVE function-calling channel — never emit the call as text, an array, or "
    "inside ``` fences.\n"
    "3. EXACT ARGUMENT SHAPES: times = 24-hour zero-padded 'HH:MM' strings; dates = 'YYYY-MM-DD' "
    "or the user's words (never invent the year). plan_day.tasks / plan_day.fixed / "
    "schedule_tasks.tasks / replace_day.blocks are JSON arrays of objects with the required keys.\n"
    "4. ONE TOOL PER BULK JOB: plan_day / schedule_tasks / replace_day / shift_blocks / "
    "add_recurring — don't loop single add_block calls for a bulk change.\n"
    "5. After multi-step edits, call list_blocks, fix anything in its CONFLICTS section, then "
    "confirm in ONE short sentence.\n"
)

def model_guidance(model: str) -> str:
    """Extensively detailed, model-specific addendum to the system prompt, chosen by
    matching the model tag. Targets each family's known weaknesses on this tool-heavy
    scheduling task."""
    m = (model or "").lower()
    if "deepseek" in m or "r1" in m:
        return _R1_GUIDANCE
    if "gpt-oss" in m or "gpt_oss" in m or "gptoss" in m:
        return _GPTOSS_GUIDANCE
    if "qwen3" in m:
        return _QWEN3_GUIDANCE
    if "qwen2" in m or "qwen-2" in m or "qwen2.5" in m:
        return _QWEN25_GUIDANCE
    if "gemma" in m:
        return _GEMMA_GUIDANCE
    if "glm" in m:
        return _GLM_GUIDANCE
    if "mistral" in m or "mixtral" in m:
        return _MISTRAL_GUIDANCE
    return _GENERIC_GUIDANCE


def model_profile(model: str) -> Optional[Dict]:
    """User-facing profile for a model tag, or None if it isn't a curated pick.
    Exact tag match first, then family/prefix so `qwen3:14b-q4_K_M` still maps."""
    tag = (model or "").strip().split("@", 1)[0]
    if not tag:
        return None
    if tag in MODEL_PROFILES:
        return MODEL_PROFILES[tag]
    low = tag.lower()
    for key, prof in MODEL_PROFILES.items():
        if key.lower() == low:
            return prof
    # Prefix / quant suffix: curated `qwen3:14b` matches `qwen3:14b-q4_K_M`
    for key, prof in sorted(MODEL_PROFILES.items(), key=lambda kv: -len(kv[0])):
        k = key.lower()
        if low.startswith(k) and (len(low) == len(k) or low[len(k)] in ":-_"):
            return prof
    # Family match for size-tagged keys (`deepseek-r1:14b` ↔ `deepseek-r1:14b-…`)
    # and untagged keys (`gemma4` ↔ `gemma4:e4b` / `gemma4:latest`).
    for key, prof in sorted(MODEL_PROFILES.items(), key=lambda kv: -len(kv[0])):
        k = key.lower()
        k_name, _, k_size = k.partition(":")
        low_name, _, low_rest = low.partition(":")
        if low_name != k_name:
            continue
        if not k_size:
            return prof
        if low_rest == k_size or low_rest.startswith(k_size + "-") or low_rest.startswith(k_size + "_"):
            return prof
    return None


def model_when_text(model: str) -> str:
    """One short paragraph for tooltips / the Settings helper under the picker."""
    p = model_profile(model)
    if not p:
        return (
            "Custom / unlisted model. This app is tool-heavy — prefer a model with "
            "strong function-calling. Verify plan/edit requests before trusting it."
        )
    return f"{p['badge']}  ·  VRAM {p['vram']}  ·  download {p['disk']}\n{p['when']}"


def model_guide_text() -> str:
    """Full multi-model guide for the Settings / AI-panel model guide dialog."""
    lines = [
        "This app edits your schedule via tool calls, so tool-calling reliability "
        "matters more than raw size. Pull with:  ollama pull <tag>",
        "",
        "Pick by GPU VRAM (Task Manager → GPU, or nvidia-smi / rocm-smi):",
        "  • 12–16 GB  →  qwen3:14b  (recommended daily driver)",
        "  • 16 GB tight  →  mistral-small3.1:24b  (best quality; unload other apps)",
        "  • Plenty of VRAM (20 GB+)  →  glm-4.7-flash is an option (~19 GB download)",
        "  • ~8 GB or less  →  ollama pull qwen3:8b (not in the curated list; slower)",
        "",
    ]
    for tag, p in MODEL_PROFILES.items():
        lines.append(f"── {tag}  ({p['badge']})")
        lines.append(f"   VRAM {p['vram']}  ·  download {p['disk']}")
        lines.append(f"   {p['when']}")
        lines.append("")
    lines.append(
        "After pulling, pick the tag in Settings or the AI panel. Press ▶ to start "
        "Ollama; ⏏ unloads the model and ⏻ stops the server (zero GPU use until ▶)."
    )
    return "\n".join(lines)


def show_model_guide(parent=None):
    """Scrollable model guide (QMessageBox truncates long text on some platforms)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Which model should I use?")
    dlg.resize(520, 480)
    lay = QVBoxLayout(dlg)
    body = QTextEdit()
    body.setReadOnly(True)
    body.setPlainText(model_guide_text())
    body.setStyleSheet(
        f"QTextEdit {{ background: {C_BG.name()}; color: {C_TEXT.name()}; "
        f"border: 1px solid {C_BORDER.name()}; border-radius: {RAD}px; "
        f"padding: 8px; font-size: 12px; }}")
    lay.addWidget(body)
    close = QPushButton("Close")
    close.setStyleSheet(
        f"QPushButton {{ background:{C_ACCENT.name()}; color:{C_ON_ACCENT.name()}; "
        f"border:none; padding:7px 18px; border-radius:{RAD}px; font-weight:bold; }}")
    close.clicked.connect(dlg.accept)
    row = QHBoxLayout(); row.addStretch(); row.addWidget(close)
    lay.addLayout(row)
    dlg.exec()

# ══════════════════════════════════════════════════════════════════════════
#  TIMELINE WIDGET  (custom-painted — pure Qt, no browser)
# ══════════════════════════════════════════════════════════════════════════
class TimelineWidget(QWidget):
    block_create_req    = Signal(int, int)   # start_min, end_min — drag/click to create
    activity_delete_req = Signal(str)        # activity id
    activity_edit_req   = Signal(str)        # activity id — open the edit dialog
    activity_changed    = Signal(str, int, int)  # id, new_start, new_end (drag move/resize)

    SNAP   = 5    # minutes — drag/resize snaps to this grid (5-min precision)
    EDGE_PX = 7   # pixels near a block's top/bottom that trigger resize

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cal_events:  List[Dict] = []
        self.activities:  List[Dict] = []
        self._hover_min:  Optional[int]   = None   # snapped minute under cursor
        self._drag_start: Optional[int]   = None   # snapped minute where create-drag began
        self._drag_cur:   Optional[int]   = None   # snapped minute under cursor while creating
        # move / resize of an existing user block
        self._edit_id:    Optional[str]   = None
        self._edit_mode:  Optional[str]   = None   # "move" | "resize_top" | "resize_bottom"
        self._edit_orig:  Optional[tuple] = None   # (start, end) at press
        self._press_min:  Optional[int]   = None   # unsnapped minute at press
        self._preview:    Optional[tuple] = None   # (id, start, end) live during drag
        self._moved:      bool            = False  # did the cursor actually move?
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(min_to_y(DAY_END) + 24)

    def _snap(self, minute: int) -> int:
        m = round(minute / self.SNAP) * self.SNAP
        return max(DAY_START, min(DAY_END, m))

    def set_data(self, cal, acts, view_date=None):
        # Timed calendar events only on the timeline; all-day uses the day banner.
        self.cal_events = timed_cal_events(cal or [])
        self.activities = acts
        self.view_date  = view_date or date.today()
        self.update()

    # ── helpers ────────────────────────────────────────────────────────────
    def _all_blocks(self):
        return sorted(
            [{"_btype": "calendar", **e} for e in self.cal_events] +
            [{"_btype": "user",     **e} for e in self.activities],
            key=lambda x: x["startMin"],
        )

    def _free_intervals(self):
        occ = [(b["startMin"], b["endMin"]) for b in self._all_blocks()]
        return _free_slots(occ)

    # ── painting ───────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), C_BG)
        self._draw_grid(p)
        self._draw_free(p)
        self._draw_events(p)
        self._draw_drag(p)
        self._draw_now(p)

    def _draw_grid(self, p: QPainter):
        lbl_font = QFont("Segoe UI", 8)
        p.setFont(lbl_font)
        for h in range(DAY_START_H, DAY_END_H + 1):
            y = min_to_y(h * 60)
            p.setPen(QPen(C_BORDER, 1))
            p.drawLine(GUTTER_W, y, self.width(), y)
            if h < DAY_END_H:
                yh = min_to_y(h * 60 + 30)
                pen = QPen(C_GRID, 1, Qt.DashLine)
                p.setPen(pen)
                p.drawLine(GUTTER_W, yh, self.width(), yh)
            lbl = f"{h:02d}:00"
            p.setPen(C_MUTED)
            p.drawText(QRect(0, y - 8, GUTTER_W - 6, 18),
                       Qt.AlignRight | Qt.AlignVCenter, lbl)

    def _draw_free(self, p: QPainter):
        # Subtle highlight of the free interval under the cursor (only when not dragging)
        if self._drag_start is not None or self._hover_min is None:
            return
        for s, e in self._free_intervals():
            if not (s <= self._hover_min <= e):
                continue
            dur = e - s
            if dur < 5:
                return
            y = min_to_y(s)
            h = max(min_to_y(e) - y, 12)
            x = GUTTER_W + 4
            w = self.width() - GUTTER_W - 8
            rect = QRect(x, y, w, h)
            fill = QColor(C_ACCENT); fill.setAlpha(18)
            p.setPen(Qt.NoPen); p.setBrush(fill)
            p.drawRect(rect)
            pen = QPen(C_ACCENT, 1, Qt.DashLine)
            pen.setColor(QColor(C_ACCENT.red(), C_ACCENT.green(), C_ACCENT.blue(), 100))
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawRect(rect.adjusted(0, 0, -1, -1))
            if dur >= 20:
                p.setPen(QColor(C_ACCENT.red(), C_ACCENT.green(), C_ACCENT.blue(), 180))
                p.setFont(QFont("Segoe UI", 9))
                p.drawText(rect.adjusted(10, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           "＋ drag to create, or click")
            return

    def _draw_drag(self, p: QPainter):
        if self._drag_start is None or self._drag_cur is None:
            return
        s, e = sorted((self._drag_start, self._drag_cur))
        if e - s < self.SNAP:
            e = s + self.SNAP  # always show at least one snap-cell while dragging
        y = min_to_y(s)
        h = max(min_to_y(e) - y, 6)
        x = GUTTER_W + 4
        w = self.width() - GUTTER_W - 8
        rect = QRect(x, y, w, h)
        fill = QColor(C_ACCENT); fill.setAlpha(70)
        p.setPen(Qt.NoPen); p.setBrush(fill)
        p.drawRect(rect)
        p.setPen(QPen(C_ACCENT, 1.5)); p.setBrush(Qt.NoBrush)
        p.drawRect(rect.adjusted(0, 0, -1, -1))
        p.setPen(C_TEXT)
        p.setFont(QFont("Segoe UI", 9, QFont.Bold))
        p.drawText(rect.adjusted(10, 4, -8, -4), Qt.AlignTop | Qt.AlignLeft,
                   f"{fmt_time(s)} – {fmt_time(e)}  ·  {fmt_dur(e - s)}")

    def _layout_blocks(self):
        """Return [(block, QRect)] for every block, using committed times.
        Shared by painting and mouse hit-testing so they always agree."""
        area_w = self.width() - GUTTER_W - 8
        out = []
        for blk in assign_overlap_cols(self._all_blocks()):
            y  = min_to_y(blk["startMin"])
            # Floor must stay <= the height of the shortest real block (a 5-min break is
            # 8px) so short blocks never overrun the next one. 20px caused breaks to
            # visually overlap the following study block.
            h  = max(min_to_y(blk["endMin"]) - y, 6)
            cw = area_w / blk["_tcols"]
            x  = int(GUTTER_W + 4 + blk["_col"] * cw)
            w  = int(cw - 4)
            out.append((blk, QRect(x, y, w, h)))
        return out

    def _user_block_at(self, x: int, y: int):
        """Topmost user (editable) block whose rect contains (x, y), or None."""
        hit = None
        for blk, rect in self._layout_blocks():
            if blk.get("_btype") == "user" and rect.contains(int(x), int(y)):
                hit = (blk, rect)   # later (higher column) blocks win
        return hit

    def _draw_events(self, p: QPainter):
        fn_bold  = QFont("Segoe UI", 9, QFont.Bold)
        fn_small = QFont("Segoe UI", 8)

        for blk, rect in self._layout_blocks():
            # apply live drag preview to the block being moved/resized
            if self._preview and blk.get("_btype") == "user" and blk["id"] == self._preview[0]:
                ps, pe = self._preview[1], self._preview[2]
                y = min_to_y(ps); h = max(min_to_y(pe) - y, 6)
                rect = QRect(rect.x(), y, rect.width(), h)
                blk  = {**blk, "startMin": ps, "endMin": pe}

            dur  = blk["endMin"] - blk["startMin"]
            x, y, h = rect.x(), rect.y(), rect.height()

            c, bg = block_colors(blk.get("color") or C_ACCENT.name())
            rr   = max(4, min(RAD + 2, rect.height() // 2, 10))
            dragging = (self._preview and blk.get("_btype") == "user"
                        and blk["id"] == self._preview[0])
            paint_schedule_block(p, rect, bg, c, radius=rr, accent_w=3,
                                 outline=bool(dragging))

            tr = rect.adjusted(10, 4, -6, -4)
            if dur >= 25:
                p.setFont(fn_bold); p.setPen(c)
                p.drawText(tr, Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap, blk["title"])
                if dur >= 40:
                    p.setFont(fn_small)
                    p.setPen(QColor(c.red(), c.green(), c.blue(), 170))
                    fm_h = QFontMetrics(fn_bold).height()
                    sub  = QRect(tr.left(), tr.top() + fm_h + 2, tr.width(), tr.height())
                    p.drawText(sub, Qt.AlignTop | Qt.AlignLeft,
                               f"{fmt_time(blk['startMin'])} – {fmt_time(blk['endMin'])}  ·  {fmt_dur(dur)}")
            else:
                p.setFont(fn_small); p.setPen(c)
                p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft, blk["title"])

    def _draw_now(self, p: QPainter):
        if getattr(self, "view_date", date.today()) != date.today():
            return
        now = datetime.now()
        nm  = now.hour * 60 + now.minute
        if not (DAY_START <= nm <= DAY_END):
            return
        y = min_to_y(nm)
        p.setPen(Qt.NoPen); p.setBrush(C_NOW)
        p.drawEllipse(GUTTER_W - 5, y - 4, 9, 9)
        p.setPen(QPen(C_NOW, 2)); p.setBrush(Qt.NoBrush)
        p.drawLine(GUTTER_W + 4, y, self.width(), y)

    # ── mouse ──────────────────────────────────────────────────────────────
    def _edit_mode_for(self, rect: QRect, y: int) -> str:
        """Resize if near a tall-enough block's top/bottom edge, else move."""
        if rect.height() >= 2 * self.EDGE_PX + 6:
            if y - rect.top() <= self.EDGE_PX:
                return "resize_top"
            if rect.bottom() - y <= self.EDGE_PX:
                return "resize_bottom"
        return "move"

    def mouseMoveEvent(self, ev):
        x = ev.position().x() if hasattr(ev, "position") else ev.x()
        y = int(ev.position().y()) if hasattr(ev, "position") else ev.y()

        # ── live move / resize of an existing block ─────────────────────────
        if self._edit_mode:
            self._moved = True
            delta = y_to_min(y) - self._press_min
            os_, oe = self._edit_orig
            dur = oe - os_
            if self._edit_mode == "move":
                ns = self._snap(os_ + delta)
                ns = max(DAY_START, min(ns, DAY_END - dur))
                self._preview = (self._edit_id, ns, ns + dur)
            elif self._edit_mode == "resize_top":
                ns = self._snap(os_ + delta)
                ns = max(DAY_START, min(ns, oe - self.SNAP))
                self._preview = (self._edit_id, ns, oe)
            else:  # resize_bottom
                ne = self._snap(oe + delta)
                ne = min(DAY_END, max(ne, os_ + self.SNAP))
                self._preview = (self._edit_id, os_, ne)
            self.update()
            return

        if x < GUTTER_W and self._drag_start is None:
            if self._hover_min is not None:
                self._hover_min = None; self.update()
            self.setCursor(Qt.ArrowCursor); return

        # ── creating a new block by dragging empty space ────────────────────
        if self._drag_start is not None:
            self._drag_cur = self._snap(y_to_min(y))
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return

        # ── hover feedback: resize cursor on edges, hand over blocks ────────
        hit = self._user_block_at(x, y)
        if hit:
            mode = self._edit_mode_for(hit[1], y)
            self.setCursor(Qt.SizeVerCursor if mode.startswith("resize")
                           else Qt.OpenHandCursor)
            if self._hover_min is not None:
                self._hover_min = None; self.update()
        else:
            snapped = self._snap(y_to_min(y))
            self.setCursor(Qt.PointingHandCursor)
            if snapped != self._hover_min:
                self._hover_min = snapped
                self.update()

    def mousePressEvent(self, ev):
        x = ev.position().x() if hasattr(ev, "position") else ev.x()
        y = int(ev.position().y()) if hasattr(ev, "position") else ev.y()
        if ev.button() != Qt.LeftButton or x < GUTTER_W:
            return
        hit = self._user_block_at(x, y)
        if hit:
            # start a move / resize on the existing block (a no-move release = edit)
            blk, rect = hit
            self._edit_id   = blk["id"]
            self._edit_mode = self._edit_mode_for(rect, y)
            self._edit_orig = (blk["startMin"], blk["endMin"])
            self._press_min = y_to_min(y)
            self._preview   = (blk["id"], blk["startMin"], blk["endMin"])
            self._moved     = False
            self.update()
            return
        # otherwise begin creating a block
        self._drag_start = self._snap(y_to_min(y))
        self._drag_cur   = self._drag_start
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return

        # ── finish a move / resize (or treat a no-move click as "edit") ─────
        if self._edit_mode:
            aid = self._edit_id
            preview, moved, orig = self._preview, self._moved, self._edit_orig
            self._edit_mode = self._edit_id = self._edit_orig = None
            self._press_min = self._preview = None
            self.update()
            if moved and preview and (preview[1], preview[2]) != orig:
                self.activity_changed.emit(aid, preview[1], preview[2])
            else:
                self.activity_edit_req.emit(aid)   # a plain click → open editor
            return

        # ── finish creating a block ─────────────────────────────────────────
        if self._drag_start is None:
            return
        s, e = sorted((self._drag_start, self._drag_cur))
        self._drag_start = self._drag_cur = None
        self.update()
        if e - s >= self.SNAP:
            self.block_create_req.emit(s, e)
        else:
            occ = sorted((b["startMin"], b["endMin"]) for b in self._all_blocks())
            end = min(s + 60, DAY_END)
            for os_, oe in occ:
                if os_ >= e and os_ < end:
                    end = os_
                    break
            if end - s >= self.SNAP:
                self.block_create_req.emit(s, end)

    def contextMenuEvent(self, ev):
        x = ev.x(); y = ev.y()
        if x < GUTTER_W:
            return
        hit = self._user_block_at(x, y)
        if not hit:
            return
        act = hit[0]
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()};
                     border: 1px solid {C_BORDER2.name()}; padding: 4px; }}
            QMenu::item {{ padding: 6px 14px; border-radius: {RAD}px; }}
            QMenu::item:selected {{ background: {C_SURF2.name()}; }}
        """)
        edit_act = menu.addAction(f"✏  Edit '{act['title']}'…")
        del_act  = menu.addAction(f"🗑  Delete '{act['title']}'")
        chosen = menu.exec(ev.globalPos())
        if chosen == edit_act:
            self.activity_edit_req.emit(act["id"])
        elif chosen == del_act:
            self.activity_delete_req.emit(act["id"])

    def leaveEvent(self, _ev):
        if self._drag_start is None and self._edit_mode is None:
            self._hover_min = None
            self.update()

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
            QDialog   {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()}; }}
            QLabel    {{ background: transparent; color: {C_TEXT.name()}; }}
            QTimeEdit, QLineEdit {{
                background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_TEXT.name()}; padding: 7px 10px; border-radius: {RAD}px;
            }}
            QTimeEdit:focus, QLineEdit:focus {{ border-color: {C_ACCENT.name()}; }}
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
        if end_min >= DAY_END:
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
            ql.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 10px;")
            col.addWidget(ql); col.addWidget(w)
            trow.addLayout(col)
        lay.addLayout(trow)

        # Optional title
        ql2 = QLabel("TITLE (optional)")
        ql2.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 10px;")
        lay.addWidget(ql2)
        self.txt = QLineEdit(placeholderText="What are you up to?")
        if is_edit:
            self.txt.setText(existing.get("title", ""))
        lay.addWidget(self.txt)

        # Buttons
        brow = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {C_BORDER.name()};
            color: {C_MUTED.name()}; padding: 8px 16px; border-radius: {RAD}px; }}
            QPushButton:hover {{ color: {C_TEXT.name()}; border-color: {C_BORDER2.name()}; }}
        """)
        cancel.clicked.connect(self.reject)
        if is_edit:
            delete = QPushButton("Delete")
            delete.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: 1px solid {_rgba(C_ERR, .5)};
                color: {C_ERR_TXT.name()}; padding: 8px 16px; border-radius: {RAD}px; }}
                QPushButton:hover {{ background: {_rgba(C_ERR, .15)}; border-color: {C_ERR.name()}; }}
            """)
            delete.clicked.connect(self._delete)
            brow.addWidget(delete)
        brow.addStretch()
        save = QPushButton("Save Changes" if is_edit else "Add to Schedule")
        save.setStyleSheet(f"""
            QPushButton {{ background: {C_ACCENT.name()}; color: {C_ON_ACCENT.name()}; padding: 8px 16px;
            border-radius: {RAD}px; font-weight: bold; border: none; }}
            QPushButton:hover {{ background: {C_ACCENT2.name()}; }}
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
        em = coerce_end_min(sm, en.hour() * 60 + en.minute())
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

def _splitter_qss() -> str:
    """Thin, theme-aware drag handles between resizable sections."""
    return f"""
        QSplitter::handle {{
            background: {C_BORDER.name()};
        }}
        QSplitter::handle:hover {{
            background: {C_ACCENT.name()};
        }}
        QSplitter::handle:horizontal {{ width: 4px; }}
        QSplitter::handle:vertical   {{ height: 4px; }}
    """

# ══════════════════════════════════════════════════════════════════════════
#  SIDEBAR  (activity type picker + daily summary — vertically resizable)
# ══════════════════════════════════════════════════════════════════════════
class SidebarWidget(QWidget):
    type_selected = Signal(str)
    split_changed = Signal()   # sizes dragged — MainWindow persists

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(170)
        self.setMaximumWidth(340)
        self.setStyleSheet(f"""
            QWidget {{ background: {C_SURFACE.name()}; }}
            QLabel  {{ background: transparent; color: {C_TEXT.name()}; }}
        """)
        self._sel = "study"
        self._type_btns: Dict[str, tuple] = {}

        lay = QVBoxLayout(self)
        lay.setSpacing(0); lay.setContentsMargins(0, 0, 0, 0)

        self._split = QSplitter(Qt.Vertical)
        self._split.setChildrenCollapsible(False)
        self._split.setHandleWidth(5)
        self._split.setStyleSheet(_splitter_qss())

        # ── Add activity (type picker scrolls; height set by splitter) ─────
        add_sec = QWidget()
        add_sec.setMinimumHeight(90)
        al = QVBoxLayout(add_sec)
        al.setContentsMargins(12, 12, 12, 8); al.setSpacing(6)

        hl = QLabel("ADD ACTIVITY")
        hl.setStyleSheet(
            f"font-size: 9px; font-weight: bold; letter-spacing: 1px; color: {C_MUTED.name()};")
        al.addWidget(hl)

        grid_host = QWidget()
        grid = QGridLayout(grid_host); grid.setSpacing(5); grid.setContentsMargins(0, 0, 0, 0)
        for i, at in enumerate(ACTIVITY_TYPES):
            btn = QPushButton(f"{at['icon']} {at['label']}")
            btn.setCheckable(True)
            btn.setChecked(at["id"] == "study")
            btn.setToolTip(at["label"])
            self._set_chip_style(btn, at, at["id"] == "study")
            btn.clicked.connect(lambda _, aid=at["id"]: self._select(aid))
            self._type_btns[at["id"]] = (btn, at)
            grid.addWidget(btn, i // 2, i % 2)
        type_scroll = QScrollArea()
        type_scroll.setWidgetResizable(True)
        type_scroll.setFrameShape(QFrame.Shape.NoFrame)
        type_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        type_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        type_scroll.setWidget(grid_host)
        al.addWidget(type_scroll, 1)

        hint = QLabel("Pick a type, then drag the timeline\n(or click for a quick 1-hour block).")
        hint.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 10px;")
        al.addWidget(hint)
        self._split.addWidget(add_sec)

        # ── Summary — tight stack like the original (no stretched gaps) ────
        sum_sec = QWidget()
        sum_sec.setMinimumHeight(80)
        sl = QVBoxLayout(sum_sec)
        sl.setContentsMargins(12, 12, 12, 8); sl.setSpacing(6)

        sh = QLabel("TODAY'S SUMMARY")
        sh.setStyleSheet(
            f"font-size: 9px; font-weight: bold; letter-spacing: 1px; color: {C_MUTED.name()};")
        sl.addWidget(sh)

        sum_scroll = QScrollArea()
        sum_scroll.setWidgetResizable(True)
        sum_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sum_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sum_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        sum_inner = QWidget()
        self._sum_area = QVBoxLayout(sum_inner)
        self._sum_area.setContentsMargins(0, 0, 0, 0)
        self._sum_area.setSpacing(6)   # same as original
        self._sum_area.setAlignment(Qt.AlignTop)
        self._sum_area.addStretch()
        sum_scroll.setWidget(sum_inner)
        sl.addWidget(sum_scroll, 1)
        self._split.addWidget(sum_sec)

        self._split.setStretchFactor(0, 1)
        self._split.setStretchFactor(1, 1)
        self._split.setSizes([220, 280])
        self._split.splitterMoved.connect(lambda *_: self.split_changed.emit())
        lay.addWidget(self._split)

    def split_sizes(self) -> list:
        return list(self._split.sizes())

    def apply_split_sizes(self, sizes):
        if isinstance(sizes, (list, tuple)) and len(sizes) >= 2 and all(int(s) > 0 for s in sizes[:2]):
            self._split.setSizes([int(sizes[0]), int(sizes[1])])

    def _set_chip_style(self, btn, at, selected):
        style_activity_type_chip(btn, at, selected, compact=True)

    def _select(self, tid):
        self._sel = tid
        for aid, (btn, at) in self._type_btns.items():
            btn.setChecked(aid == tid)
            self._set_chip_style(btn, at, aid == tid)
        self.type_selected.emit(tid)

    @property
    def selected_type(self): return self._sel

    def update_summary(self, cal_events, activities):
        while self._sum_area.count():
            item = self._sum_area.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        # All-day events have no duration on the timeline — exclude from totals.
        all_b = timed_cal_events(cal_events or []) + list(activities or [])
        DAY_T = DAY_END - DAY_START
        totals: Dict[str, int] = {}
        for b in all_b:
            totals[b["type"]] = totals.get(b["type"], 0) + (b["endMin"] - b["startMin"])

        cats = [
            {"id": "calendar", "label": "Meetings", "color": C_INFO.name()},
        ] + [{"id": t["id"], "label": t["label"], "color": t["color"]} for t in ACTIVITY_TYPES]

        for cat in cats:
            mins = totals.get(cat["id"], 0)
            if not mins: continue
            row = QWidget()
            # Fixed size so the VBox doesn't stretch rows apart when the
            # section is taller than the content (matches original packing).
            row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            rl  = QVBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(3)

            top = QHBoxLayout(); top.setSpacing(6)
            dot = QLabel("●"); dot.setStyleSheet(f"color: {cat['color']}; font-size: 9px;")
            lbl = QLabel(cat["label"]); lbl.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 11px;")
            val = QLabel(fmt_dur(mins)); val.setStyleSheet(f"color: {C_TEXT.name()}; font-size: 11px; font-weight: bold;")
            top.addWidget(dot); top.addWidget(lbl, 1); top.addWidget(val)
            rl.addLayout(top)

            bar = QProgressBar()
            bar.setFixedHeight(3)
            bar.setTextVisible(False)
            bar.setRange(0, DAY_T)
            bar.setValue(mins)
            bar.setStyleSheet(f"""
                QProgressBar {{ background: {C_BORDER.name()}; border-radius: {RAD}px; border: none; }}
                QProgressBar::chunk {{ background: {cat['color']}; border-radius: {RAD}px; }}
            """)
            rl.addWidget(bar)
            self._sum_area.addWidget(row)
        self._sum_area.addStretch()   # leftover space below the list, not between rows

# ══════════════════════════════════════════════════════════════════════════
#  WEEK VIEW  (7 columns Mon–Sun, whole day scaled per column; read-mostly v1:
#  click a block → edit dialog, click a day header → that day's Day view)
# ══════════════════════════════════════════════════════════════════════════
class WeekViewWidget(QWidget):
    day_clicked   = Signal(object)   # datetime.date — header click → Day view
    block_clicked = Signal(str)      # user activity id — open the edit dialog

    HDR_H = 34    # day-name strip
    AD_H  = 18    # all-day banner under the name (0 height when empty)
    GUT_W = 46    # time-gutter width (narrower than the Day view's GUTTER_W)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._monday = date.today() - timedelta(days=date.today().weekday())
        self._acts: Dict[str, List[Dict]] = {}   # iso date → user blocks
        self._cal:  Dict[str, List[Dict]] = {}   # iso date → read-only cal events
        self._block_hits: List[tuple] = []       # (QRect, activity id) — user blocks
        self._hdr_hits:   List[tuple] = []       # (QRect, datetime.date)
        self.setMinimumSize(720, 480)
        self.setMouseTracking(True)

    def set_week(self, monday: date, acts_by_date: Dict, cal_by_date: Dict):
        self._monday = monday
        self._acts   = acts_by_date
        self._cal    = cal_by_date
        self.update()

    def days(self) -> List[date]:
        return [self._monday + timedelta(days=i) for i in range(7)]

    def _top_h(self) -> int:
        """Header + optional all-day row if any day this week has an all-day event."""
        any_ad = any(allday_cal_events(self._cal.get(d.isoformat(), []))
                     for d in self.days())
        return self.HDR_H + (self.AD_H if any_ad else 0)

    def _y(self, minutes: int) -> int:
        """Per-column minute→y: the full 24h day scaled to fit under the header
        (an overview — no scrolling, unlike the Day timeline)."""
        top = self._top_h()
        span = self.height() - top
        return int(top + (minutes - DAY_START) / (DAY_END - DAY_START) * span)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), C_BG)
        self._block_hits = []
        self._hdr_hits   = []

        days  = self.days()
        top_h = self._top_h()
        any_ad = top_h > self.HDR_H
        cw    = (self.width() - self.GUT_W) / 7.0
        today = date.today()

        # hour grid + gutter labels (every 2 h — one hour is ~30 px here)
        p.setFont(QFont("Segoe UI", 7))
        for h in range(DAY_START_H, DAY_END_H + 1):
            y = self._y(h * 60)
            p.setPen(QPen(C_GRID if h % 2 else C_BORDER, 1))
            p.drawLine(self.GUT_W, y, self.width(), y)
            if h % 2 == 0 and h < DAY_END_H:
                p.setPen(C_MUTED)
                p.drawText(QRect(0, y - 8, self.GUT_W - 6, 16),
                           Qt.AlignRight | Qt.AlignVCenter, f"{h:02d}:00")

        fn_hdr_d = QFont("Segoe UI", 8, QFont.Bold)
        fn_chip  = QFont("Segoe UI", 8)
        fn_tiny  = QFont("Segoe UI", 7)
        fm_chip  = QFontMetrics(fn_chip)
        fm_tiny  = QFontMetrics(fn_tiny)

        for i, d in enumerate(days):
            x0 = int(self.GUT_W + i * cw)
            x1 = int(self.GUT_W + (i + 1) * cw)

            # column separator
            p.setPen(QPen(C_BORDER, 1))
            p.drawLine(x0, top_h, x0, self.height())

            # blocks: TIMED calendar events + user activities only (all-day is in header)
            ds  = d.isoformat()
            blk = sorted(
                [{"_btype": "calendar", **e} for e in timed_cal_events(self._cal.get(ds, []))] +
                [{"_btype": "user",     **e} for e in self._acts.get(ds, [])],
                key=lambda b: (b["startMin"], b["endMin"]))
            area_w = cw - 5
            for b in assign_overlap_cols(blk):
                by = self._y(b["startMin"])
                bh = max(self._y(b["endMin"]) - by, 3)
                bw = area_w / b["_tcols"]
                bx = int(x0 + 3 + b["_col"] * bw)
                rect = QRect(bx, by, int(bw - 2), bh)
                c, bg = block_colors(b.get("color") or C_ACCENT.name())
                rr = max(3, min(RAD, rect.height() // 2, 8))
                paint_schedule_block(p, rect, bg, c, radius=rr, accent_w=2)
                if b["_btype"] == "user":
                    self._block_hits.append((rect, b["id"]))
                if bh >= 26:
                    p.setPen(c); p.setFont(fn_chip)
                    tr = rect.adjusted(5, 2, -3, -2)
                    p.drawText(tr, Qt.AlignTop | Qt.AlignLeft,
                               fm_chip.elidedText(b.get("title", ""), Qt.ElideRight, tr.width()))
                    if bh >= 30:   # start time tucked right under the title
                        p.setFont(fn_tiny)
                        p.setPen(QColor(c.red(), c.green(), c.blue(), 170))
                        p.drawText(QRect(tr.left(), tr.top() + fm_chip.height() + 1,
                                         tr.width(), 12),
                                   Qt.AlignTop | Qt.AlignLeft, fmt_time(b["startMin"]))
                elif bh >= 11:
                    p.setPen(c); p.setFont(fn_tiny)
                    tr = rect.adjusted(4, 0, -2, 0)
                    p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft,
                               fm_chip.elidedText(b.get("title", ""), Qt.ElideRight, tr.width()))

            # now line across today's column only
            if d == today:
                nm = datetime.now().hour * 60 + datetime.now().minute
                if DAY_START <= nm <= DAY_END:
                    ny = self._y(nm)
                    p.setPen(QPen(C_NOW, 2))
                    p.drawLine(x0 + 1, ny, x1, ny)
                    p.setPen(Qt.NoPen); p.setBrush(C_NOW)
                    p.drawEllipse(x0 - 3, ny - 3, 7, 7)

            # header last, on top — click target for "open this day"
            hdr = QRect(x0, 0, int(cw), top_h)
            self._hdr_hits.append((hdr, d))
            p.setBrush(C_SURFACE); p.setPen(Qt.NoPen)
            p.drawRect(hdr)
            p.setPen(QPen(C_BORDER, 1))
            p.drawLine(x0, top_h, x1, top_h)
            if i:
                p.drawLine(x0, 0, x0, top_h)
            name_rect = QRect(x0, 0, int(cw), self.HDR_H)
            lbl = d.strftime("%a %d")
            if d == today:
                p.setPen(Qt.NoPen); p.setBrush(C_ACCENT)
                w = QFontMetrics(fn_hdr_d).horizontalAdvance(lbl) + 16
                p.drawRoundedRect(QRect(name_rect.center().x() - w // 2, 7, w, 20), RAD, RAD)
                p.setPen(C_ON_ACCENT)
            else:
                p.setPen(C_TEXT)
            p.setFont(fn_hdr_d)
            p.drawText(name_rect, Qt.AlignCenter, lbl)

            # all-day strip under the day name (holidays, due dates, spirit week)
            if any_ad:
                ads = allday_cal_events(self._cal.get(ds, []))
                ad_rect = QRect(x0 + 2, self.HDR_H, int(cw) - 4, self.AD_H - 2)
                if ads:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(C_INFO.red(), C_INFO.green(), C_INFO.blue(), 40))
                    p.drawRoundedRect(ad_rect, RAD, RAD)
                    p.setPen(C_INFO); p.setFont(fn_tiny)
                    text = " · ".join(e.get("title", "") for e in ads)
                    p.drawText(ad_rect.adjusted(4, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm_tiny.elidedText(text, Qt.ElideRight, ad_rect.width() - 8))

        # gutter/header corner + outer frame line under the header row
        p.setPen(QPen(C_BORDER, 1))
        p.drawLine(self.GUT_W, 0, self.GUT_W, self.height())

    # ── mouse: hover cursor + click targets ─────────────────────────────────
    def _hit(self, pos):
        for rect, aid in reversed(self._block_hits):   # later-drawn (higher col) wins
            if rect.contains(pos):
                return ("block", aid)
        for rect, d in self._hdr_hits:
            if rect.contains(pos):
                return ("day", d)
        return None

    def mouseMoveEvent(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        self.setCursor(Qt.PointingHandCursor if self._hit(pos) else Qt.ArrowCursor)

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        hit = self._hit(pos)
        if not hit:
            return
        kind, val = hit
        if kind == "block":
            self.block_clicked.emit(val)
        else:
            self.day_clicked.emit(val)

# ══════════════════════════════════════════════════════════════════════════
#  MONTH VIEW  (Google-Calendar-style month grid)
# ══════════════════════════════════════════════════════════════════════════
class MonthViewWidget(QWidget):
    day_clicked = Signal(object)   # datetime.date

    HDR_H = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self._year  = date.today().year
        self._month = date.today().month
        self._events: Dict[str, List[Dict]] = {}
        self._hits: List[tuple] = []
        self.setMinimumHeight(480)
        self.setCursor(Qt.PointingHandCursor)

    def set_month(self, year, month, events_by_date):
        self._year, self._month = year, month
        self._events = events_by_date
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), C_BG)
        self._hits = []

        weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(self._year, self._month)
        cw = self.width() / 7.0
        ch = (self.height() - self.HDR_H) / len(weeks)
        today = date.today()

        p.setFont(QFont("Segoe UI", 8, QFont.Bold))
        p.setPen(C_MUTED)
        for i, nm in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]):
            p.drawText(QRect(int(i * cw), 0, int(cw), self.HDR_H), Qt.AlignCenter, nm)

        fn_day  = QFont("Segoe UI", 9)
        fn_chip = QFont("Segoe UI", 8)
        fm_chip = QFontMetrics(fn_chip)

        for r, week in enumerate(weeks):
            for c, d in enumerate(week):
                x = int(c * cw); y = int(self.HDR_H + r * ch)
                cell = QRect(x, y, int(cw), int(ch))
                self._hits.append((cell, d))

                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(C_BORDER, 1))
                p.drawRect(cell)

                in_month = (d.month == self._month)
                if d == today:
                    p.setBrush(C_ACCENT); p.setPen(Qt.NoPen)
                    p.drawEllipse(QRect(x + 5, y + 3, 20, 20))
                    p.setPen(C_ON_ACCENT)
                else:
                    p.setPen(C_TEXT if in_month else C_GHOST)
                p.setFont(fn_day)
                p.drawText(QRect(x + 5, y + 3, 20, 20), Qt.AlignCenter, str(d.day))

                evs = sorted(self._events.get(d.isoformat(), []),
                             key=lambda b: b.get("startMin", 0))
                if not evs:
                    continue
                max_chips = max(0, int((ch - 30) // 17))
                shown = evs[:max_chips]
                p.setFont(fn_chip)
                for i, ev in enumerate(shown):
                    cy   = y + 27 + i * 17
                    chip = QRect(x + 4, int(cy), int(cw) - 8, 14)
                    col, bg = block_colors(ev.get("color") or C_ACCENT.name())
                    if not in_month:
                        col = QColor(col.red(), col.green(), col.blue(), 120)
                        bg  = QColor(col.red(), col.green(), col.blue(), max(28, BLOCK_FILL_A // 2))
                    p.setPen(Qt.NoPen); p.setBrush(bg)
                    p.drawRoundedRect(chip, 4, 4)
                    p.setPen(col)
                    label = f"{fmt_time(ev.get('startMin', 0))} {ev.get('title', '')}"
                    p.drawText(chip.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm_chip.elidedText(label, Qt.ElideRight, chip.width() - 8))
                if len(evs) > len(shown):
                    p.setPen(C_MUTED)
                    p.drawText(QRect(x + 8, int(y + 27 + len(shown) * 17), int(cw) - 12, 13),
                               Qt.AlignVCenter | Qt.AlignLeft, f"+{len(evs) - len(shown)} more")

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        for rect, d in self._hits:
            if rect.contains(pos):
                self.day_clicked.emit(d)
                return

# ══════════════════════════════════════════════════════════════════════════
#  YEAR VIEW  (12 mini-months, busy days dotted)
# ══════════════════════════════════════════════════════════════════════════
class YearViewWidget(QWidget):
    day_clicked = Signal(object)   # datetime.date

    def __init__(self, parent=None):
        super().__init__(parent)
        self._year = date.today().year
        self._busy: set = set()
        self._hits: List[tuple] = []
        self.setMinimumSize(860, 660)
        self.setCursor(Qt.PointingHandCursor)

    def set_year(self, year, busy_dates):
        self._year = year
        self._busy = {b for b in busy_dates if b}
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), C_BG)
        self._hits = []
        today = date.today()

        cols, rows = 4, 3
        mw = self.width()  / cols
        mh = self.height() / rows
        fn_title = QFont("Segoe UI", 10, QFont.Bold)
        fn_hdr   = QFont("Segoe UI", 7)
        fn_day   = QFont("Segoe UI", 8)

        for m in range(1, 13):
            ox = ((m - 1) % cols) * mw + 14
            oy = ((m - 1) // cols) * mh + 10

            p.setFont(fn_title); p.setPen(C_ACCENT)
            p.drawText(QRect(int(ox), int(oy), int(mw) - 28, 18),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       date(self._year, m, 1).strftime("%B"))

            cw  = (mw - 28) / 7.0
            chh = (mh - 50) / 7.0
            rad = max(3, int(min(cw, chh) / 2) - 1)

            p.setFont(fn_hdr); p.setPen(C_MUTED)
            for i, ltr in enumerate("SMTWTFS"):
                p.drawText(QRect(int(ox + i * cw), int(oy + 20), int(cw), int(chh)),
                           Qt.AlignCenter, ltr)

            weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(self._year, m)
            for r, week in enumerate(weeks):
                for c, d in enumerate(week):
                    if d.month != m:
                        continue
                    cell = QRect(int(ox + c * cw), int(oy + 20 + (r + 1) * chh),
                                 int(cw), int(chh))
                    self._hits.append((cell, d))
                    p.setFont(fn_day)
                    if d == today:
                        p.setBrush(C_ACCENT); p.setPen(Qt.NoPen)
                        p.drawEllipse(cell.center(), rad, rad)
                        p.setPen(C_ON_ACCENT)
                    elif d.isoformat() in self._busy:
                        bg = QColor(C_ACCENT); bg.setAlpha(55)
                        p.setBrush(bg); p.setPen(Qt.NoPen)
                        p.drawEllipse(cell.center(), rad, rad)
                        p.setPen(C_TEXT)
                    else:
                        p.setPen(C_MUTED)
                    p.setBrush(Qt.NoBrush)
                    p.drawText(cell, Qt.AlignCenter, str(d.day))

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        for rect, d in self._hits:
            if rect.contains(pos):
                self.day_clicked.emit(d)
                return

# ══════════════════════════════════════════════════════════════════════════
#  AI ASSISTANT PANEL
# ══════════════════════════════════════════════════════════════════════════
class AIPanel(QWidget):
    def __init__(self, get_ctx_fn, parent=None):
        super().__init__(parent)
        self.get_ctx    = get_ctx_fn
        self.model       = DEFAULT_MODEL
        self.temperature = DEFAULT_SETTINGS["temperature"]
        self.num_ctx     = DEFAULT_SETTINGS["num_ctx"]
        self.on_model_edited = None       # set by MainWindow to persist model changes
        self.mode       = "chat"
        # Restore last transcript (v3.8.0) so an OOM/kill doesn't eat the chat.
        self.history: Dict[str, List[Dict]] = load_chat_histories()
        self._thread: Optional[OllamaThread] = None
        self._check_thread: Optional[OllamaCheckThread] = None  # status poll (v3.7.1)
        self._cur_text  = ""
        self._ollama_up = False
        self._mem_warned: set = set()     # models we already soft-warned this session
        self.execute_tool = None          # set by MainWindow: fn(name, args) -> str
        self.on_turn_start = None         # set by MainWindow: snapshot schedule for undo
        self.on_turn_end = None           # set by MainWindow: unlock Undo, drop no-op snapshots
        self.on_undo = None               # set by MainWindow: restore the last snapshot
        self._loop_msgs: List[Dict] = []  # running conversation for the tool loop
        self._depth = 0                   # tool-round counter (loop guard)

        # Preferred width when the body splitter shows this panel; user can drag.
        self._panel_w = 340
        self.setObjectName("aiPanel")
        self.setMinimumWidth(220)
        self.setMaximumWidth(560)
        self.setStyleSheet(
            f"#aiPanel {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()}; "
            f"border-left: 1px solid {C_BORDER.name()}; }}")

        lay = QVBoxLayout(self); lay.setSpacing(0); lay.setContentsMargins(0,0,0,0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet(f"border-bottom: 1px solid {C_BORDER.name()};")
        hl  = QVBoxLayout(hdr); hl.setContentsMargins(12,10,12,8); hl.setSpacing(6)

        tr = QHBoxLayout()
        t  = QLabel("Assistant"); t.setStyleSheet("font-size: 13px; font-weight: bold;")
        tr.addWidget(t)
        self._dot = QLabel("●"); self._dot.setStyleSheet(f"color: {C_MUTED.name()};")
        self._stxt = QLabel("Checking…"); self._stxt.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 11px;")
        tr.addWidget(self._dot); tr.addWidget(self._stxt); tr.addStretch()

        self._unload_btn = QPushButton("⏏")
        self._unload_btn.setToolTip("Unload model from memory (keeps Ollama running)")
        self._unload_btn.setFixedSize(26, 24)
        self._unload_btn.setCursor(Qt.PointingHandCursor)
        self._unload_btn.setEnabled(False)
        self._unload_btn.setStyleSheet(f"""
            QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_MUTED.name()}; border-radius: {RAD}px; font-size: 12px; }}
            QPushButton:hover {{ background: {_rgba(C_ACCENT, .18)}; border-color: {C_ACCENT.name()}; color: {C_ACCENT.name()}; }}
            QPushButton:disabled {{ color: {C_BORDER2.name()}; border-color: {C_BORDER.name()}; }}
        """)
        self._unload_btn.clicked.connect(self._unload_model)
        tr.addWidget(self._unload_btn)

        self._power_btn = QPushButton("▶")
        self._power_btn.setFixedSize(26, 24)
        self._power_btn.setCursor(Qt.PointingHandCursor)
        self._power_btn.clicked.connect(self._toggle_power)
        tr.addWidget(self._power_btn)
        self._set_power_state(False)
        hl.addLayout(tr)

        mr = QHBoxLayout()
        mr.addWidget(QLabel("Model:", styleSheet=f"color:{C_MUTED.name()}; font-size:10px;"))
        self._model_in = QComboBox(); self._model_in.setEditable(True)
        self._model_in.setFixedHeight(24)
        self._model_in.addItems(self._model_choices())
        self._model_in.setCurrentText(self.model)
        self._model_in.setStyleSheet(f"""
            QComboBox {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_TEXT.name()}; padding: 2px 6px; border-radius: {RAD}px; font-size: 11px; }}
            QComboBox QAbstractItemView {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()};
            selection-background-color: {C_SURF2.name()}; }}
        """)
        self._model_in.currentTextChanged.connect(self._on_model_changed)
        mr.addWidget(self._model_in, 1)
        self._model_info_btn = QPushButton("?")
        self._model_info_btn.setFixedSize(22, 24)
        self._model_info_btn.setCursor(Qt.PointingHandCursor)
        self._model_info_btn.setToolTip("When to use each model")
        self._model_info_btn.setStyleSheet(f"""
            QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_MUTED.name()}; border-radius: {RAD}px; font-size: 11px; font-weight: bold; }}
            QPushButton:hover {{ background: {_rgba(C_ACCENT, .18)}; border-color: {C_ACCENT.name()};
            color: {C_ACCENT.name()}; }}
        """)
        self._model_info_btn.clicked.connect(self._show_model_guide)
        mr.addWidget(self._model_info_btn)
        self._pull_btn = QPushButton("⬇")
        self._pull_btn.setFixedSize(22, 24)
        self._pull_btn.setCursor(Qt.PointingHandCursor)
        self._pull_btn.setToolTip("Download this model with Ollama")
        self._pull_btn.setStyleSheet(f"""
            QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_MUTED.name()}; border-radius: {RAD}px; font-size: 11px; }}
            QPushButton:hover {{ background: {_rgba(C_OK, .18)}; border-color: {C_OK.name()};
            color: {C_OK_TXT.name()}; }}
            QPushButton:disabled {{ color: {C_BORDER2.name()}; }}
        """)
        self._pull_btn.clicked.connect(self._pull_selected_model)
        mr.addWidget(self._pull_btn)
        hl.addLayout(mr)
        self._model_hint = QLabel()
        self._model_hint.setWordWrap(True)
        self._model_hint.setStyleSheet(
            f"color:{C_MUTED.name()}; font-size:10px; padding:0 2px;")
        hl.addWidget(self._model_hint)
        self._pull_prog = QLabel()
        self._pull_prog.setWordWrap(True)
        self._pull_prog.setStyleSheet(
            f"color:{C_ACCENT.name()}; font-size:10px; padding:0 2px 2px 2px;")
        self._pull_prog.hide()
        hl.addWidget(self._pull_prog)
        self._pull_thread: Optional[OllamaPullThread] = None
        self._refresh_model_hint()
        lay.addWidget(hdr)

        # Tabs
        tabs = QWidget(); tabs.setStyleSheet(f"border-bottom: 1px solid {C_BORDER.name()};")
        tl   = QHBoxLayout(tabs); tl.setContentsMargins(0,0,0,0); tl.setSpacing(0)
        self._tabs = {}
        for mode, lbl in [("chat","Chat"), ("plan","Plan"), ("suggest","Analyze")]:
            b = QPushButton(lbl); b.setCheckable(True); b.setChecked(mode=="chat")
            b.setStyleSheet(self._tab_style(mode == "chat"))
            b.clicked.connect(lambda _, m=mode: self._set_mode(m))
            self._tabs[mode] = b; tl.addWidget(b)
        tl.addStretch()
        # Undo the assistant's last schedule change (enabled once it makes one).
        self._undo_btn = QPushButton("↶ Undo")
        self._undo_btn.setEnabled(False)
        self._undo_btn.setCursor(Qt.PointingHandCursor)
        self._undo_btn.setToolTip(
            "Undo the assistant's last change (Ctrl+Z undoes your own edits)")
        self._undo_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C_MUTED.name()}; border: none;"
            f" padding: 4px 12px; font-size: 11px; }}"
            f"QPushButton:hover:enabled {{ color: {C_TEXT.name()}; }}"
            f"QPushButton:disabled {{ color: {C_BORDER2.name()}; }}")
        self._undo_btn.clicked.connect(self._do_undo)
        tl.addWidget(self._undo_btn)
        lay.addWidget(tabs)

        # Messages
        self._msgs_view = QTextEdit()
        self._msgs_view.setReadOnly(True)
        self._msgs_view.setStyleSheet(f"""
            QTextEdit {{ background: {C_BG.name()}; border: none;
            color: {C_TEXT.name()}; font-size: 12px; padding: 8px; }}
        """)
        lay.addWidget(self._msgs_view, 1)

        self._thinking = QLabel("⟳  Thinking…")
        self._thinking.setStyleSheet(f"color:{C_MUTED.name()}; font-size:11px; padding:4px 12px;")
        self._thinking.hide()
        lay.addWidget(self._thinking)

        # Input
        inp = QWidget(); il = QVBoxLayout(inp); il.setContentsMargins(8,6,8,8); il.setSpacing(4)
        self._inp = QTextEdit()
        self._inp.setMaximumHeight(72)
        self._inp.setPlaceholderText("Ask me anything about your day…")
        self._inp.setStyleSheet(f"""
            QTextEdit {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_TEXT.name()}; padding: 6px; border-radius: {RAD}px; font-size: 12px; }}
            QTextEdit:focus {{ border-color: {C_ACCENT.name()}; }}
        """)
        il.addWidget(self._inp)

        br = QHBoxLayout()
        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setStyleSheet(f"background:{_rgba(C_ERR, .2)}; color:{C_ERR_TXT.name()}; border-radius:{RAD}px; padding:3px 10px;")
        self._stop_btn.hide()
        self._stop_btn.clicked.connect(self._stop)

        send = QPushButton("Send ↑")
        send.setStyleSheet(f"""
            QPushButton {{ background:{C_ACCENT.name()}; color:{C_ON_ACCENT.name()}; border-radius:{RAD}px;
            font-weight:bold; padding:5px 14px; border:none; }}
            QPushButton:hover {{ background:{C_ACCENT2.name()}; }}
        """)
        send.clicked.connect(self._send)
        br.addWidget(self._stop_btn); br.addStretch(); br.addWidget(send)
        il.addLayout(br)
        lay.addWidget(inp)

        self._render()
        self._poll_ollama()
        self._timer = QTimer(self); self._timer.timeout.connect(self._poll_ollama); self._timer.start(30_000)

    def _model_choices(self):
        seen, out = set(), []
        # Installed first, then curated recommendations not yet present
        installed = list_ollama_models()
        for m in installed + RECOMMENDED_MODELS:
            if m and m not in seen:
                seen.add(m); out.append(m)
        return out

    def _refresh_model_list(self):
        """Rebuild the picker after a pull / Ollama reconnect."""
        cur = self.model
        self._model_in.blockSignals(True)
        self._model_in.clear()
        self._model_in.addItems(self._model_choices())
        self._model_in.setCurrentText(cur)
        self._model_in.blockSignals(False)
        self._refresh_model_hint()

    def _on_model_changed(self, text):
        self.model = text.strip() or DEFAULT_MODEL
        self._refresh_model_hint()
        if callable(self.on_model_edited):
            self.on_model_edited(self.model)

    def _refresh_model_hint(self):
        """Show badge / install state + when-to-use tooltip; enable ⬇ if missing."""
        installed = list_ollama_models() if self._ollama_up else []
        have = model_is_installed(self.model, installed) if self._ollama_up else False
        p = model_profile(self.model)
        if not self._ollama_up:
            status = "Ollama not running"
        elif have:
            status = "Installed"
        else:
            status = "Not installed — click ⬇ to download"
        if p:
            self._model_hint.setText(f"{p['badge']}  ·  {p['vram']} VRAM  ·  {status}")
        else:
            self._model_hint.setText(f"Custom model  ·  {status}")
        tip = model_when_text(self.model)
        if not have and self._ollama_up:
            tip += f"\n\nNot installed. Pull with:  ollama pull {self.model}"
        self._model_in.setToolTip(tip)
        self._model_hint.setToolTip(tip)
        pulling = self._pull_thread is not None and self._pull_thread.isRunning()
        self._pull_btn.setEnabled(self._ollama_up and bool(self.model) and not have and not pulling)
        self._pull_btn.setToolTip(
            "Download this model with Ollama" if not have else "Already installed")

    def _pull_selected_model(self):
        tag = (self._model_in.currentText() or self.model or "").strip()
        if not tag:
            return
        if not self._ollama_up:
            QMessageBox.information(self, "Ollama", "Start Ollama (▶) before pulling a model.")
            return
        if model_is_installed(tag):
            self._refresh_model_hint(); return
        if self._pull_thread is not None and self._pull_thread.isRunning():
            return
        self._pull_prog.setText(f"Pulling {tag}…"); self._pull_prog.show()
        self._pull_btn.setEnabled(False)
        t = OllamaPullThread(tag)
        t.progress.connect(self._on_pull_progress)
        t.finished_ok.connect(self._on_pull_ok)
        t.failed.connect(self._on_pull_fail)
        t.finished.connect(t.deleteLater)
        t.finished.connect(lambda: setattr(self, "_pull_thread", None))
        self._pull_thread = t
        t.start()

    def _on_pull_progress(self, msg: str):
        self._pull_prog.setText(msg); self._pull_prog.show()

    def _on_pull_ok(self, tag: str):
        self._pull_prog.setText(f"✓  {tag} ready"); self._pull_prog.show()
        self._refresh_model_list()
        QTimer.singleShot(4000, lambda: self._pull_prog.hide()
                          if not (self._pull_thread and self._pull_thread.isRunning()) else None)

    def _on_pull_fail(self, msg: str):
        self._pull_prog.setText(f"Pull failed: {msg}"); self._pull_prog.show()
        self._refresh_model_hint()
        QMessageBox.warning(self, "Model pull", msg)

    def _show_model_guide(self):
        show_model_guide(self)

    def apply_settings(self, s):
        """Apply persisted AI settings — on launch and after the Settings dialog."""
        self._settings   = dict(s or {})
        self.model       = s.get("model", DEFAULT_MODEL)
        self.temperature = float(s.get("temperature", 0.3))
        self.num_ctx     = int(s.get("num_ctx", 16384))
        self._model_in.blockSignals(True)
        self._model_in.setCurrentText(self.model)
        self._model_in.blockSignals(False)
        self._refresh_model_hint()

    def _tab_style(self, active):
        return (f"QPushButton {{ background:transparent; border:none; border-bottom:2px solid {C_ACCENT.name()};"
                f"color:{C_ACCENT.name()}; padding:8px 4px; font-size:12px; }}" if active else
                f"QPushButton {{ background:transparent; border:none; border-bottom:2px solid transparent;"
                f"color:{C_MUTED.name()}; padding:8px 4px; font-size:12px; }}"
                f"QPushButton:hover {{ color:{C_TEXT.name()}; }}")

    def _poll_ollama(self):
        """Kick a status check. Hold the QThread ref until finished — dropping it
        left the only Python reference dying during main-thread GC while the C++
        thread was still mid-request (segfault in crash.log 2026-07-07). Same
        pattern as MainWindow._check_for_update / _update_thread."""
        if self._check_thread is not None and self._check_thread.isRunning():
            return
        t = OllamaCheckThread()                       # unparented; ref held below
        t.result.connect(self._on_ollama)
        t.finished.connect(t.deleteLater)
        t.finished.connect(lambda: setattr(self, "_check_thread", None))
        self._check_thread = t
        t.start()

    def _on_ollama(self, ok: bool):
        was = self._ollama_up
        self._ollama_up = ok
        self._dot.setStyleSheet(f"color: {(C_OK if ok else C_ERR).name()};")
        if not self._stxt.text().startswith("Starting"):
            self._stxt.setText("Connected" if ok else "Not running")
        self._set_power_state(ok)
        self._unload_btn.setEnabled(ok)
        # Refresh install-state labels when Ollama comes up (or drops).
        if ok != was or ok:
            self._refresh_model_hint()
            if ok and not was:
                self._refresh_model_list()

    def _set_power_state(self, up: bool):
        """Power button is a toggle: ▶ Start when down, ⏻ Stop when up."""
        if up:
            self._power_btn.setText("⏻")
            self._power_btn.setToolTip("Stop Ollama (shuts down the local LLM server)")
            self._power_btn.setStyleSheet(f"""
                QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_MUTED.name()}; border-radius: {RAD}px; font-size: 13px; }}
                QPushButton:hover {{ background: {_rgba(C_ERR, .18)}; border-color: {C_ERR.name()}; color: {C_ERR_TXT.name()}; }}
            """)
        else:
            self._power_btn.setText("▶")
            self._power_btn.setToolTip("Start Ollama (launches the local LLM server)")
            self._power_btn.setStyleSheet(f"""
                QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_MUTED.name()}; border-radius: {RAD}px; font-size: 12px; }}
                QPushButton:hover {{ background: {_rgba(C_OK, .18)}; border-color: {C_OK.name()}; color: {C_OK_TXT.name()}; }}
            """)

    def _toggle_power(self):
        if self._ollama_up:
            self._shutdown_ollama()
        else:
            self._start_ollama()

    def _start_ollama(self):
        ok, msg = start_ollama(getattr(self, "_settings", None))
        if not ok:
            QMessageBox.information(self, "Ollama", msg)
            return
        self._stxt.setText("Starting…")
        self._dot.setStyleSheet(f"color: {C_WARN.name()};")  # amber while booting
        # Server takes a moment to bind the port — poll a few times as it comes up
        for delay in (700, 1500, 2500, 4000, 6000, 9000):
            QTimer.singleShot(delay, self._poll_ollama)

    def _unload_model(self):
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        ok, msg = unload_ollama_model(self.model)
        self._poll_ollama()  # server stays up, so dot should remain green
        QMessageBox.information(self, "Ollama", msg)

    def _shutdown_ollama(self):
        confirm = QMessageBox.question(
            self, "Stop Ollama",
            "Stop the local Ollama server?\n\n"
            "The AI assistant won't respond until you start it again "
            "(the ▶ button, the Ollama app, or 'ollama serve').",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        ok, msg = stop_ollama()
        self._poll_ollama()  # refresh status dot immediately
        if not ok:
            QMessageBox.information(self, "Ollama", msg)

    def _set_mode(self, mode):
        self.mode = mode
        for m, b in self._tabs.items():
            b.setChecked(m == mode)
            b.setStyleSheet(self._tab_style(m == mode))
        hints = {"plan": "Describe what you need to accomplish today…",
                 "suggest": "Ask for specific suggestions…"}
        self._inp.setPlaceholderText(hints.get(mode, "Ask me anything about your day…"))
        self._render()
        if mode == "suggest" and not self.history["suggest"]:
            QTimer.singleShot(200, lambda: self._generate(None))

    def _render(self):
        msgs = self.history[self.mode]
        if not msgs:
            hints = {
                "plan":    "Tell me what you need to accomplish today and I'll build a schedule.",
                "suggest": "Analyzing your schedule…",
            }
            h = hints.get(self.mode, "Ask about your day, tasks, or how to be more productive.")
            self._msgs_view.setHtml(
                f'<p style="color:{C_MUTED.name()}; font-style:italic; text-align:center; margin-top:20px;">{h}</p>')
            return

        html = ""
        for msg in msgs:
            c = msg["content"].replace("&","&amp;").replace("<","&lt;").replace("\n","<br>")
            r = msg["role"]
            if r == "user":
                html += (f'<div style="text-align:right;margin:4px 0;">'
                         f'<span style="background:{C_ACCENT.name()};color:{C_ON_ACCENT.name()};padding:6px 10px;'
                         f'border-radius:{RAD}px;display:inline-block;max-width:88%;font-size:12px;">'
                         f'{c}</span></div>')
            elif r == "assistant":
                html += (f'<div style="margin:4px 0;">'
                         f'<span style="background:{C_SURF2.name()};border:1px solid {C_BORDER.name()};'
                         f'color:{C_TEXT.name()};padding:8px 10px;border-radius:{RAD}px;'
                         f'display:inline-block;font-size:12px;white-space:pre-wrap;">{c}</span></div>')
            elif r == "tool_note":
                html += (f'<div style="margin:4px 0;background:{_rgba(C_OK, .08)};'
                         f'border:1px solid {_rgba(C_OK, .25)};color:{C_OK_TXT.name()};padding:6px 8px;'
                         f'border-radius:{RAD}px;font-size:11px;">{c}</div>')
            elif r == "error":
                html += (f'<div style="margin:4px 0;background:{_rgba(C_ERR, .1)};'
                         f'border:1px solid {_rgba(C_ERR, .3)};color:{C_ERR_TXT.name()};padding:8px;'
                         f'border-radius:{RAD}px;font-size:12px;">{c}</div>')
        self._msgs_view.setHtml(html)
        self._msgs_view.verticalScrollBar().setValue(self._msgs_view.verticalScrollBar().maximum())

    def _persist_chat(self, *, force: bool = False):
        """Best-effort write of the transcript (throttled mid-stream)."""
        save_chat_histories(self.history, force=force)

    def _maybe_memory_warning(self):
        """Soft, once-per-model-per-session note before a generate. Never blocks."""
        if self.model in self._mem_warned:
            return
        warn = memory_warning_for(self.model)
        if not warn:
            return
        self._mem_warned.add(self.model)
        self.history[self.mode].append({"role": "error", "content": warn})
        self._persist_chat(force=True)

    def _sys_prompt(self):
        ctx = self.get_ctx()
        p = (
            "You are the scheduling assistant built into Daily Scheduler, a desktop "
            "day-planner. You help the user (a high-school student) plan study, projects, "
            "exercise, downtime, and social time, and you edit their calendar directly "
            "with tools.\n\n"
            "RIGHT NOW\n"
            f"It is {ctx.get('weekday', '')}, {ctx.get('today', '')} at "
            f"{fmt_time(ctx.get('now_min', 0))} (24-hour clock). "
            f"The day on screen is {ctx.get('view_date', '')}"
            f"{' — that is today.' if ctx.get('viewing_today') else ' (not today).'}\n"
            "Use this real date and time to judge urgency and deadlines, to resolve "
            "'today' / 'tomorrow' / weekday names, and — when scheduling on today — to "
            "avoid placing anything earlier than the current time.\n\n"
            "THE DAY\n"
            "Anything the user asks for without a date goes on the day on screen. For "
            "another day the user names it (e.g. \"Thursday\", \"tomorrow\", \"6/14\") — pass "
            "that word STRAIGHT into the tool's date argument; the app resolves the exact "
            "date. Do NOT convert a weekday or 'tomorrow' into a calendar date yourself — your "
            "date arithmetic is unreliable, and the app does it correctly. So for \"copy to "
            "Thursday\" pass to_date=\"Thursday\", NOT a date you counted out. "
            "Omit the date for the day on screen.\n\n"
            + activity_type_prompt_block() + "\n\n"
            "SCHEDULE (day on screen)\n"
            "Google Calendar (READ-ONLY — you cannot move or delete these). Timed events are "
            "FIXED obstacles — schedule around them. All-day events (holidays, due dates, "
            "spirit week) do not block free time but are real deadlines/context you must "
            "respect when planning:\n")
        cal = ctx.get("cal_events", [])
        ads = allday_cal_events(cal)
        tms = sorted(timed_cal_events(cal), key=lambda e: e["startMin"])
        if ads:
            p += "All-day:\n"
            p += "".join(f"  - {e['title']} (all day)\n" for e in ads)
        if tms:
            p += "Timed (fixed):\n"
            p += "".join(f"  - {e['title']}: {fmt_time(e['startMin'])}–{fmt_time(e['endMin'])}\n"
                         for e in tms)
        if not ads and not tms:
            p += "  (none)\n"
        p += "Your editable blocks:\n"
        acts = ctx.get("activities", [])
        p += "".join(f"  - \"{a['title']}\" [{a['type']}]: "
                     f"{fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}\n"
                     for a in acts) or "  (none yet)\n"
        week = ctx.get("week_ahead", "")
        if week:
            p += ("\nTHE WEEK AHEAD (read-only Google Calendar, next 7 days from today — "
                  "use for deadlines and planning ahead; each is a FIXED obstacle on its "
                  "day). Pass the date the user names when scheduling on one of these days:\n"
                  + week + "\n")
        p += (
            "\nTOOLS — pick the ONE that fits; never chain small calls for a bulk job:\n"
            "  add_block      – add one block\n"
            "  add_recurring  – add the same block to many days (weekdays/weekends/daily or a dates list)\n"
            "  move_block     – change a block's time, length, day, or title (match by title and/or 'at')\n"
            "  delete_block   – remove a block by title and/or 'at' (its start time)\n"
            "  clear_range    – delete blocks within a time window (\"clear my afternoon\")\n"
            "  clear_day      – wipe a whole day\n"
            "  shift_blocks   – move EVERY block on a day by an offset (\"push everything 2h later\")\n"
            "  copy_day       – duplicate all blocks from one day to another (\"copy today to 6/14\")\n"
            "  split_block    – split one block into focus chunks with breaks (pomodoro)\n"
            "  schedule_tasks – PLAN: fit UNORDERED tasks into free time safely (never deletes)\n"
            "  plan_day       – BUILD a full day: ORDERED tasks + fixed anchors (meals/workout) "
            "+ chunking, laid out around meetings (rebuilds the day). Best for 'X then Y, lunch "
            "at 13:00, workout at 16:00, 30-min chunks with breaks'.\n"
            "  make_room      – add ONE fixed appointment at a set time to an ALREADY-PLANNED day "
            "and shift the existing blocks around it WITHOUT deleting them (keep some 'pin'ned). "
            "Best for 'I have a meeting 12:00–13:30, adjust my day around it'.\n"
            "  find_free_time – (read-only) list open gaps; use to answer \"when am I free?\"\n"
            "  reflow_from_now– push the rest of today later/earlier when running late\n"
            "  plan_for_deadline – spread work across the days before a due date\n"
            "  week_summary   – (read-only) time per category over a week; balance check\n"
            "  replace_day    – rebuild a whole day from a complete list (full reset).\n"
            "                   It DELETES blocks you don't include, so list everything to keep.\n"
            "  list_blocks    – read a day's schedule\n\n"
            "PLANNING — when the user asks you to plan their day or fit tasks in, REASON it out, "
            "then use schedule_tasks:\n"
            "  - Infer sensible durations if not given (homework ~1h, big project ~2h, quick "
            "errand ~30m); ask only if truly unclear. Pass each as minutes.\n"
            "  - Judge urgency from wording: urgent / due today / ASAP → priority \"high\" (it gets "
            "an earlier slot); \"sometime\" / \"if I have time\" → \"low\".\n"
            "  - Use 'prefer' (morning/afternoon/evening or a time) when the task has a natural "
            "time. Keep to waking hours via day_start/day_end (defaults to the user's waking-hours "
            "setting; on today, planning starts no earlier than the current time) unless the "
            "user is an early bird / night owl. Never plan work in the middle of the night.\n"
            "  - schedule_tasks places tasks around existing blocks and calendar events and NEVER "
            "deletes them — so meals, classes, and anything the user keeps are safe automatically. "
            "Prefer it over replace_day for planning. Only use replace_day for an explicit "
            "from-scratch rebuild (and then include every block to keep). Verify with list_blocks.\n"
            "  - USE plan_day (not replace_day, not hand-built lists) whenever the request has an "
            "ORDER ('X first, then Y'), FIXED times (lunch at 13:00, workout at 16:00), and/or "
            "chunking with breaks. Give each task its TOTAL focus minutes (NEVER subtract break "
            "time — breaks are added on top), set chunk/break, and pass the fixed items in 'fixed'. "
            "The app lays everything out in order, splits into chunks, and flows the rest past the "
            "anchors and meetings — so you don't compute any times yourself. Don't shrink a task's "
            "minutes to 'make room' for breaks; plan_day handles that.\n"
            "  - For a NEW fixed appointment in an ALREADY-PLANNED day ('I have a meeting at 3pm, "
            "rearrange around it'), use make_room — ONE call that drops the appointment at the "
            "exact time and shifts the existing blocks around it without deleting any (pass 'pin' "
            "for blocks that must not move, e.g. a workout). Do NOT use add_block (it would just "
            "drop the appointment in a random free slot), and do NOT chain move_block calls.\n\n"
            "RULES\n"
            "  - ALWAYS call a tool when asked to add/move/remove/rename/copy/clear/shift/plan "
            "— never just describe the change. Saying you did it without a tool call is a "
            "failure — the schedule only changes when a tool runs.\n"
            "  - Times are 24-hour HH:MM. For another day pass the user's OWN word for it — a "
            "weekday name (\"Thursday\"), \"tomorrow\", or \"6/14\" — NOT a date you computed; "
            "omit it for the day on screen.\n"
            "  - Blocks can't overlap — the app auto-adjusts, so don't fuss over exact gaps.\n"
            "  - To delete/move/rename a block, identify it by title and/or by its start "
            "time using 'at' (24h HH:MM). To remove ONE time slot, use 'at' with that "
            "block's start time — e.g. delete the 2pm block → delete_block(at=\"14:00\"). "
            "When several blocks share a title, add 'at' to pick the exact one. Don't "
            "delete by title alone if the user pointed at a specific time. To wipe the "
            "WHOLE day use clear_day.\n"
            "  - Google Calendar events are READ-ONLY; if asked to change one, say it must "
            "be edited in Google Calendar. PLAN AROUND THEM: when you shift / rebuild / reflow "
            "a day the app automatically pushes your blocks off any meeting, but you should "
            "still arrange things sensibly around it (e.g. resume the displaced work right "
            "after the meeting ends rather than leaving a hole).\n"
            "  - CHECK YOUR WORK: after any edit — especially several at once or a whole-day "
            "rebuild — call list_blocks. It ends with a CONFLICTS section that flags every "
            "overlap (block-on-block AND block-on-meeting). If it lists conflicts, fix them "
            "and call list_blocks again. Also confirm the right blocks exist, times/durations "
            "match the request, and nothing was deleted by accident. Repeat until it reports "
            "'No conflicts' — only then tell the user it's done.\n"
            "  - After it's verified, confirm in one short sentence — don't restate the whole "
            "schedule.\n"
            "  - Be friendly and concise.\n\n"
            "EXAMPLES\n"
            "  \"delete the 2pm block\"             → delete_block(at=\"14:00\")\n"
            "  \"delete the 9am study block\"       → delete_block(title=\"study\", at=\"09:00\")\n"
            "  \"remove my gym session\"           → delete_block(title=\"gym\")\n"
            "  \"move the 9am block to 11\"         → move_block(at=\"09:00\", start=\"11:00\")\n"
            "  \"move AP work to 1pm\"              → move_block(title=\"AP work\", start=\"13:00\")\n"
            "  \"make gym 30 minutes longer\"       → move_block(title=\"gym\", end=\"...\")\n"
            "  \"copy my schedule to 6/14\"         → copy_day(to_date=\"6/14\")\n"
            "  \"copy today's schedule to Thursday\" → copy_day(to_date=\"Thursday\")  ← pass the weekday word, don't compute the date\n"
            "  \"shift everything two hours later\"  → shift_blocks(minutes=120)\n"
            "  \"clear my afternoon\"               → clear_range(start=\"12:00\", end=\"18:00\")\n"
            "  \"study 16:00-18:00 every weekday\"  → add_recurring(title=\"Study\", start=\"16:00\", end=\"18:00\", weekdays=[\"weekdays\"])\n"
            "  \"when am I free for 2 hours?\"      → find_free_time(duration=120)\n"
            "  \"split my study block into 30-min chunks\" → split_block(title=\"study\", chunk=30, break=5)\n"
            "  \"plan my day: finish the essay (urgent), gym, read; keep dinner\" → "
            "schedule_tasks(tasks=[{title:\"Finish essay\",minutes:120,priority:\"high\"}, "
            "{title:\"Gym\",minutes:60,prefer:\"evening\"}, {title:\"Read\",minutes:30,priority:\"low\"}])\n"
            "  \"college essays, then history, then AP — 2h each in 30-min chunks with 15-min "
            "breaks; lunch 13:00 for 1h; workout 16:00 for 1h; wake 10:00\" → plan_day("
            "start=\"10:00\", fixed=[{title:\"Lunch\",start:\"13:00\",minutes:60,type:\"meals\"}, "
            "{title:\"Workout\",start:\"16:00\",minutes:60,type:\"exercise\"}], tasks=["
            "{title:\"College Essays\",minutes:120,type:\"project\",chunk:30,break:15}, "
            "{title:\"History\",minutes:120,type:\"study\",chunk:30,break:15}, "
            "{title:\"AP Psychology\",minutes:120,type:\"study\",chunk:30,break:15}])  "
            "← each task keeps a full 2h of focus; breaks + lunch + workout are extra, placed around it.\n"
            "  \"I have a college-apps meeting 12:00–13:30 on 6/22 (25 min buffer each side); shift "
            "my day around it but don't move my workout\" → make_room(date=\"6/22\", title=\"College "
            "Applications Meeting\", start=\"12:00\", end=\"13:30\", buffer_before=25, buffer_after=25, "
            "pin=[\"Workout/Break\"])  ← inserts the meeting + buffers and shifts everything else "
            "around it, keeping the workout fixed; nothing is deleted.\n")
        add = {"plan": "\nThe user wants help planning. Gather what they need to get done, "
                       "reason out durations / urgency / preferred times, then place them with "
                       "ONE schedule_tasks call (it keeps existing blocks safe) and verify.",
               "suggest": "\nGive 3-5 specific, actionable schedule improvements."}.get(self.mode, "")
        return p + add + model_guidance(self.model)

    def _send(self):
        txt = self._inp.toPlainText().strip()
        if not txt: return
        self._inp.clear()
        self.history[self.mode].append({"role": "user", "content": txt})
        self._persist_chat(force=True)
        self._render(); self._generate(txt)

    def _do_undo(self):
        if callable(self.on_undo):
            self.on_undo()

    def set_undo_enabled(self, on: bool):
        self._undo_btn.setEnabled(bool(on))

    def _turn_ended(self):
        """Every way a turn finishes (final text, error, round limit, Stop) funnels
        here so MainWindow can unlock Undo exactly once per turn."""
        self._thinking.hide(); self._stop_btn.hide()
        self._persist_chat(force=True)
        if callable(self.on_turn_end):
            self.on_turn_end()

    def _generate(self, user_msg):
        if self._thread and self._thread.isRunning(): return
        self._maybe_memory_warning()
        if callable(self.on_turn_start):   # let MainWindow snapshot the schedule for undo
            self.on_turn_start()
        hist = [m for m in self.history[self.mode] if m["role"] in ("user","assistant")]
        msgs = [{"role":"system","content":self._sys_prompt()}] + \
               [{"role":m["role"],"content":m["content"]} for m in hist if m["content"]]

        self.history[self.mode].append({"role":"assistant","content":""})
        self._cur_text  = ""
        self._loop_msgs = msgs
        self._depth     = 0
        self._thinking.show(); self._stop_btn.show()
        self._persist_chat(force=True)
        self._spawn_thread()

    def _effective_temp(self):
        # Analyze/suggest mode runs a touch warmer for more varied ideas; editing modes
        # (chat/plan) stay precise for reliable tool-calling.
        return (min(1.2, self.temperature + 0.3) if self.mode == "suggest"
                else self.temperature)

    def _spawn_thread(self):
        self._thread = OllamaThread(self._loop_msgs, self.model, tools=AI_TOOLS,
                                    num_ctx=self.num_ctx, temperature=self._effective_temp())
        self._thread.token.connect(self._on_token)
        self._thread.done.connect(self._on_done)
        self._thread.tool_calls.connect(self._on_tool_calls)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_tool_calls(self, calls):
        h = self.history[self.mode]
        # drop the empty streaming bubble; tool notes take its place
        if h and h[-1]["role"] == "assistant" and not h[-1]["content"].strip():
            h.pop()
        self._loop_msgs = self._loop_msgs + [
            {"role": "assistant", "content": self._cur_text or "", "tool_calls": calls}]
        for call in calls:
            fn   = call.get("function") or {}
            name = fn.get("name", "?")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception: args = {}
            result = self.execute_tool(name, args) if callable(self.execute_tool) \
                     else "Tool execution unavailable."
            h.append({"role": "tool_note", "content": f"{name} → {result}"})
            self._loop_msgs.append({"role": "tool", "tool_name": name,
                                    "name": name, "content": str(result)})
        self._render()
        self._persist_chat(force=True)
        self._depth += 1
        if self._depth >= MAX_TOOL_ROUNDS:   # guard against tool-call loops
            self._turn_ended()
            return
        h.append({"role": "assistant", "content": ""})
        self._cur_text = ""
        self._spawn_thread()

    def _on_token(self, tok):
        self._cur_text += tok
        if looks_like_tool_text(self._cur_text):
            # Model is printing a tool call as text — don't show raw JSON; keep the
            # "Thinking…" indicator up. _on_done will execute it.
            self.history[self.mode][-1]["content"] = ""
            self._render()
        else:
            self.history[self.mode][-1]["content"] = self._cur_text
            self._render(); self._thinking.hide()
        # Mid-stream crash insurance (throttled).
        self._persist_chat(force=False)

    def _on_done(self):
        # Small models sometimes print the tool call as text (<|python_tag|>, ``` fences,
        # bare JSON, arrays…) instead of using the native tool_calls channel. Recover it.
        extracted = extract_tool_calls(self._cur_text) if self._depth < MAX_TOOL_ROUNDS else []
        if extracted:
            h = self.history[self.mode]
            if h and h[-1]["role"] == "assistant":
                h.pop()   # drop the (hidden) raw-text bubble
            self._cur_text = ""
            self._on_tool_calls([{"function": {"name": e["name"], "arguments": e["args"]}}
                                 for e in extracted])
            return
        # Not a tool call. Restore the real text (it may have been hidden mid-stream
        # because it looked tool-like), or show a fallback if it was unparseable JSON.
        h = self.history[self.mode]
        if h and h[-1]["role"] == "assistant":
            if self._cur_text and not looks_like_tool_text(self._cur_text):
                h[-1]["content"] = self._cur_text
            elif looks_like_tool_text(self._cur_text):
                h[-1]["content"] = ("I tried to update your schedule but couldn't read "
                                    "the result — could you rephrase that?")
            self._render()
        self._turn_ended()

    def _on_error(self, msg):
        self.history[self.mode].pop()
        self.history[self.mode].append({"role":"error","content":msg})
        self._render(); self._turn_ended()

    def _stop(self):
        if self._thread: self._thread.stop()
        self._persist_chat(force=True)

# ══════════════════════════════════════════════════════════════════════════
#  SETUP SCREEN
# ══════════════════════════════════════════════════════════════════════════
class SetupWidget(QWidget):
    proceed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {C_BG.name()};")
        outer = QVBoxLayout(self); outer.setAlignment(Qt.AlignCenter)

        card = QWidget(); card.setFixedWidth(520)
        card.setStyleSheet(f"""
            QWidget {{ background: {C_SURFACE.name()}; border-radius: {RAD_LG}px; color: {C_TEXT.name()}; }}
            QLabel  {{ background: transparent; }}
        """)
        cl = QVBoxLayout(card); cl.setSpacing(14); cl.setContentsMargins(40,36,40,36)

        title = QLabel("📅 Daily Scheduler")
        title.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {C_ACCENT.name()};")
        cl.addWidget(title)

        sub = QLabel("A native desktop app for planning your day.\n"
                     "Optionally connect Google Calendar or just use it offline.")
        sub.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 13px;"); sub.setWordWrap(True)
        cl.addWidget(sub)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {C_BORDER.name()};"); cl.addWidget(sep)

        gcal = QLabel("Google Calendar (optional)")
        gcal.setStyleSheet("font-size: 13px; font-weight: bold;"); cl.addWidget(gcal)

        steps = QLabel(
            "1.  console.cloud.google.com → create project → enable Calendar API\n"
            "2.  APIs & Services → Credentials → + Create Credentials\n"
            "     → OAuth 2.0 Client ID → Desktop application → Download JSON\n"
            "3.  Load that file below — the app stores it in ~/.daily-scheduler/"
        )
        steps.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 12px;"); steps.setWordWrap(True)
        cl.addWidget(steps)

        have = CREDS_FILE.exists()
        self._creds_lbl = QLabel("✓ credentials.json loaded" if have else "No credentials loaded")
        self._creds_lbl.setStyleSheet(f"color: {C_OK.name() if have else C_MUTED.name()}; font-size: 12px;")
        cl.addWidget(self._creds_lbl)

        load_btn = QPushButton("Load credentials.json…")
        load_btn.setStyleSheet(f"""
            QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_TEXT.name()}; padding: 7px 14px; border-radius: {RAD}px; font-size: 12px; border-style:solid; }}
            QPushButton:hover {{ border-color: {C_BORDER2.name()}; }}
        """)
        load_btn.clicked.connect(self._load)
        cl.addWidget(load_btn)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color: {C_BORDER.name()};"); cl.addWidget(sep2)

        ar = QHBoxLayout()
        go = QPushButton("Connect Google & Open")
        go.setStyleSheet(f"""
            QPushButton {{ background:{C_ACCENT.name()}; color:{C_ON_ACCENT.name()}; padding:9px 18px;
            border-radius:{RAD}px; font-weight:bold; border:none; font-size:13px; }}
            QPushButton:hover {{ background:{C_ACCENT2.name()}; }}
        """)
        go.clicked.connect(self._connect)

        skip = QPushButton("Use Without Google")
        skip.setStyleSheet(f"""
            QPushButton {{ background:transparent; border:1px solid {C_BORDER.name()};
            color:{C_MUTED.name()}; padding:9px 18px; border-radius:{RAD}px; font-size:13px; }}
            QPushButton:hover {{ color:{C_TEXT.name()}; border-color:{C_BORDER2.name()}; }}
        """)
        skip.clicked.connect(self.proceed.emit)
        ar.addWidget(go); ar.addWidget(skip)
        cl.addLayout(ar)

        self._warn = QLabel(""); self._warn.setStyleSheet(f"color: {C_ERR_TXT.name()}; font-size: 12px;")
        self._warn.setWordWrap(True); self._warn.hide(); cl.addWidget(self._warn)

        outer.addWidget(card)

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load credentials.json", "", "JSON (*.json)")
        if path:
            shutil.copy(path, str(CREDS_FILE))
            self._creds_lbl.setText("✓ credentials.json loaded")
            self._creds_lbl.setStyleSheet(f"color: {C_OK.name()}; font-size: 12px;")

    def _connect(self):
        if not CREDS_FILE.exists():
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
        accent = C_MUTED if is_end else C_ACCENT
        badge  = "BLOCK ENDED" if is_end else ("TEST" if kind == "test" else "STARTING NOW")

        # Soft outer margin so a faux shadow rim stays inside the translucent window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        shell = QFrame(); shell.setObjectName("alertShell")
        shell.setStyleSheet(
            f"#alertShell {{ background: {_rgba(C_BG, 0.55)}; border-radius: 6px; }}")
        outer.addWidget(shell)
        shell_l = QVBoxLayout(shell); shell_l.setContentsMargins(3, 3, 3, 3)

        card = QFrame(); card.setObjectName("alertCard")
        # Scope to #alertCard so rules don't cascade onto child QLabels (also QFrame).
        card.setStyleSheet(
            f"#alertCard {{ background: {C_SURFACE.name()}; "
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
            f"color: {C_MUTED.name()}; font-size: 10px; font-weight: 700;"
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
            f"color: {C_TEXT.name()}; font-size: 15px; font-weight: 700; padding-top: 2px;")
        col.addWidget(t)

        b = QLabel(body); b.setWordWrap(True)
        b.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 12px; line-height: 1.3;")
        col.addWidget(b)

        foot = QLabel("Click to dismiss")
        foot.setStyleSheet(f"color: {_rgba(C_MUTED, 0.75)}; font-size: 10px; padding-top: 2px;")
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
            QDialog {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()}; }}
            QLabel  {{ background: transparent; color: {C_TEXT.name()}; }}
            QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QLineEdit {{
                background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_TEXT.name()}; padding: 5px 8px; border-radius: {RAD}px; }}
            QComboBox QAbstractItemView {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()};
                selection-background-color: {C_SURF2.name()}; }}
            QCheckBox {{ color: {C_TEXT.name()}; spacing: 8px; }}
            QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_TEXT.name()}; padding: 6px 12px; border-radius: {RAD}px; }}
            QPushButton:hover {{ border-color: {C_BORDER2.name()}; }}
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
            lbl.setStyleSheet(f"color:{C_MUTED.name()}; font-size:10px; font-weight:bold; "
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
        for m in list_ollama_models() + RECOMMENDED_MODELS:
            if m and m not in seen:
                seen.add(m); models.append(m)
        self.model_cb.addItems(models)
        self.model_cb.setCurrentText(settings.get("model", DEFAULT_MODEL))
        self.model_cb.currentTextChanged.connect(self._on_settings_model_changed)
        a.addRow("Model", self.model_cb)
        self.model_hint = QLabel()
        self.model_hint.setWordWrap(True)
        self.model_hint.setStyleSheet(
            f"color:{C_MUTED.name()}; font-size:11px; padding:2px 0 4px 0;")
        a.addRow("", self.model_hint)
        guide_btn = QPushButton("When to use each model…")
        guide_btn.setCursor(Qt.PointingHandCursor)
        guide_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C_ACCENT.name()}; border: none;"
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
        models_hint.setStyleSheet(f"color:{C_MUTED.name()}; font-size:10px;")
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
        foot.setStyleSheet(f"background:{C_SURFACE.name()}; border-top:1px solid {C_BORDER.name()};")
        br = QHBoxLayout(foot); br.setContentsMargins(22, 10, 22, 14); br.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(f"QPushButton {{ background:{C_ACCENT.name()}; color:{C_ON_ACCENT.name()}; "
                           f"border:none; padding:7px 18px; border-radius:{RAD}px; font-weight:bold; }}")
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
                os.startfile(str(DATA_DIR))            # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(DATA_DIR)])
            else:
                subprocess.run(["xdg-open", str(DATA_DIR)])
        except Exception:
            pass

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export schedule", str(Path.home() / "daily-scheduler-export.json"),
            "JSON (*.json)")
        if path:
            try:
                shutil.copyfile(DATA_FILE, path)
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
            f"QPushButton {{ background:{C_ACCENT.name()}; color:{C_ON_ACCENT.name()}; "
            f"border:none; padding:7px 18px; border-radius:{RAD}px; font-weight:bold; }}")
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Daily Scheduler {APP_VERSION}")
        self.resize(1300, 860)
        self.setMinimumSize(960, 620)

        self._settings     = load_settings()
        self._creds        = None
        self._cal_by_date: Dict[str, List[Dict]] = {}
        self._fetched_keys: set = set()
        self._cal_threads: List[QThread] = []
        self._all_acts:    List[Dict] = load_all_activities()
        self._ai_undo:     List[List[Dict]] = []   # schedule snapshots for AI undo
        self._manual_undo: List[List[Dict]] = []   # v4.0: Ctrl+Z for manual edits
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
        self._update_thread = None         # in-flight UpdateCheckThread (one at a time)
        self._update_tag = self._update_url = None   # newest release found, if any
        self._notify_on    = self._settings["notify_on"]
        self._dnd_override = self._settings["dnd_override"]   # break through DND via app-drawn popup
        self._popups:      List[QWidget] = []
        self._notified:    set = set()     # (block_id, startMin) already announced today
        self._notified_ends: set = set()   # (block_id, endMin) end-chimes already fired
        self._notified_day = date.today().isoformat()
        self._really_quit  = False
        self._tray_hinted  = False

        self.setStyleSheet(f"QMainWindow {{ background: {C_BG.name()}; }}")
        self.setWindowIcon(self._make_app_icon())
        # Ctrl+Z — undo the last MANUAL edit (AI has its own ↶ Undo button)
        sc = QShortcut(QKeySequence("Ctrl+Z"), self)
        sc.setContext(Qt.WindowShortcut)
        sc.activated.connect(self._manual_undo_last)

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
        if CREDS_FILE.exists():
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
        self._scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {C_BG.name()}; }}")
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
            f"QLabel {{ background: {_rgba(C_INFO, .14)}; color: {C_INFO.name()}; "
            f"border-bottom: 1px solid {_rgba(C_INFO, .32)}; padding: 8px 14px; "
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
        self._year_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {C_BG.name()}; }}")
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
            f"background:{C_SURFACE.name()}; border-top:1px solid {C_BORDER.name()};")
        sb = QHBoxLayout(status_bar); sb.setContentsMargins(0, 0, 10, 0); sb.setSpacing(8)
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setStyleSheet(
            f"color: {C_MUTED.name()}; font-size: 11px; padding: 3px 14px; background: transparent;")
        sb.addWidget(self._status_lbl); sb.addStretch()
        # Live "Now / Next" indicator — always reflects the real current time / today's
        # schedule (independent of the day being viewed), refreshed by _now_timer.
        self._nownext_lbl = QLabel("")
        self._nownext_lbl.setStyleSheet(
            f"color: {C_TEXT.name()}; font-size: 11px; padding: 3px 8px; background: transparent;")
        sb.addWidget(self._nownext_lbl)
        self._update_btn = QPushButton("")
        self._update_btn.setCursor(Qt.PointingHandCursor)
        self._update_btn.setStyleSheet(
            f"QPushButton {{ background:{_rgba(C_ACCENT, .15)}; color:{C_ACCENT.name()};"
            f" border:1px solid {_rgba(C_ACCENT, .5)}; padding:2px 10px; border-radius:{RAD}px;"
            f" font-size:11px; font-weight:bold; }}"
            f"QPushButton:hover {{ background:{_rgba(C_ACCENT, .28)}; }}")
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
            f"background:{C_SURFACE.name()}; border-bottom:1px solid {C_BORDER.name()};")
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(18, 0, 14, 0); hl.setSpacing(8)

        def hbtn(text, checked=False):
            b = QPushButton(text)
            b.setCheckable(checked)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background:{C_SURF2.name()}; border:1px solid {C_BORDER.name()};
                color:{C_MUTED.name()}; padding:6px 14px; border-radius:{RAD}px; font-size:12px; }}
                QPushButton:hover {{ color:{C_TEXT.name()}; border-color:{C_BORDER2.name()};
                background:{_rgba(C_TEXT, .04)}; }}
                QPushButton:checked {{ background:{_rgba(C_ACCENT, .16)};
                border-color:{_rgba(C_ACCENT, .55)}; color:{C_ACCENT.name()}; font-weight:600; }}
            """)
            return b

        def icon_btn(text, tip, font_px=16):
            # Fixed-width + large padding clips emoji/glyphs (‹ › ⚙) to invisibility.
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(36, 32)
            b.setToolTip(tip)
            b.setStyleSheet(f"""
                QPushButton {{ background:{C_SURF2.name()}; border:1px solid {C_BORDER.name()};
                color:{C_TEXT.name()}; padding:0; border-radius:{RAD}px;
                font-size:{font_px}px; font-weight:600; }}
                QPushButton:hover {{ color:{C_ACCENT.name()}; border-color:{C_ACCENT.name()};
                background:{_rgba(C_ACCENT, .12)}; }}
                QPushButton:pressed {{ background:{_rgba(C_ACCENT, .22)}; }}
            """)
            return b

        logo = QLabel("◈  Daily Scheduler")
        logo.setStyleSheet(
            f"font-size:15px; font-weight:700; color:{C_ACCENT.name()}; letter-spacing:0.2px;")
        hl.addWidget(logo)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setStyleSheet(f"color:{C_MUTED.name()}; font-size:10px; padding-top:4px;")
        hl.addWidget(ver)

        prev_b = icon_btn("‹", "Previous", font_px=18)
        prev_b.clicked.connect(lambda: self._nav(-1))
        today_b = hbtn("Today")
        today_b.clicked.connect(lambda: self._goto_date(date.today()))
        next_b = icon_btn("›", "Next", font_px=18)
        next_b.clicked.connect(lambda: self._nav(1))
        hl.addWidget(prev_b); hl.addWidget(today_b); hl.addWidget(next_b)

        self._date_lbl = QLabel(datetime.now().strftime("%A, %B %d, %Y"))
        self._date_lbl.setStyleSheet(f"color:{C_TEXT.name()}; font-size:13px; font-weight:bold;")
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
            QPushButton {{ background:{C_ACCENT.name()}; color:{C_ON_ACCENT.name()}; padding:5px 13px;
            border-radius:{RAD}px; font-size:12px; border:none; }}
            QPushButton:hover {{ background:{C_ACCENT2.name()}; }}
        """)
        self._auth_btn.clicked.connect(self._auth_google)
        hl.addWidget(self._auth_btn)

        return hdr

    # ── Boot ───────────────────────────────────────────────────────────────
    def _boot(self):
        self._stack.setCurrentIndex(1)
        if CREDS_FILE.exists():
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
            QPushButton {{ background: transparent; border: 1px solid {C_BORDER.name()};
            color: {C_OK.name()}; padding: 5px 13px; border-radius: {RAD}px; font-size: 12px; }}
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
        """Start a CalFetchThread for each (key, start, end) range not already fetched."""
        for key, start, end in ranges:
            if key in self._fetched_keys:
                continue
            self._fetched_keys.add(key)
            self._set_status("Fetching calendar…")
            t = CalFetchThread(
                self._creds, start, end,
                calendar_ids=parse_calendar_ids(self._settings.get("calendar_ids", "primary")))
            t.done.connect(self._on_cal)
            t.error.connect(lambda e, k=key: (self._fetched_keys.discard(k),
                                              self._set_status(e, True)))
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

    def _free_gaps(self, ds: str, after=DAY_START, before=DAY_END):
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
                    y = max(0, min_to_y(max(now_min - 60, DAY_START)))
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
        """Push current schedule so Ctrl+Z can restore it after a manual edit."""
        self._manual_undo.append([dict(a) for a in self._all_acts])
        del self._manual_undo[:-MANUAL_UNDO_KEEP]

    def _manual_undo_last(self):
        """Ctrl+Z: restore the schedule to before the last create/edit/drag/delete."""
        if not self._manual_undo:
            self._set_status("Nothing to undo.")
            return
        self._all_acts = self._manual_undo.pop()
        save_all_activities(self._all_acts)
        self._ai_undo_invalidate()
        self._refresh_view()
        self._set_status("Undid last edit. (Ctrl+Z)")

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
                a["startMin"] = max(DAY_START, int(start))
                a["endMin"]   = min(DAY_END, max(int(end), a["startMin"] + self._timeline.SNAP))
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
            panel.setMinimumWidth(0)   # allow full collapse while hidden
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
            self._ai_turn_snapshotted = False
        self._update_undo_state()

    def _ai_snapshot_before(self, name: str):
        """Before the first schedule-changing tool of the turn, snapshot the
        current schedule so the whole turn can be undone as a single step."""
        if name in AI_READONLY_TOOLS or self._ai_turn_snapshotted:
            return
        self._ai_undo.append([dict(a) for a in self._all_acts])
        del self._ai_undo[:-AI_UNDO_KEEP]
        self._ai_turn_snapshotted = True
        self._update_undo_state()

    def _ai_undo_last(self):
        """Restore the schedule to before the assistant's most recent change."""
        if self._ai_turn_active or not self._ai_undo:
            return
        self._all_acts = self._ai_undo.pop()
        self._ai_turn_snapshotted = False   # a post-undo tool round must re-snapshot
        save_all_activities(self._all_acts)
        self._refresh_view()
        self._update_undo_state()
        self._set_status("Undid the assistant's last change.")

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
                        a["endMin"] = min(a["startMin"] + old_dur, DAY_END)
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
                    a["endMin"] = min(a["startMin"] + 60, DAY_END)
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
                    ns  = max(DAY_START, min(a["startMin"] + mins, DAY_END - dur))
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
                after  = parse_hhmm(str(args["after"]))  if args.get("after")  else DAY_START
                before = parse_hhmm(str(args["before"])) if args.get("before") else DAY_END
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
                    we = DAY_END
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
                    fe = min(fe, DAY_END)
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
                    win_s = max(DAY_START, es - bb)
                    new_fixed.append({"id": new_id(), "date": ds, "startMin": win_s, "endMin": es,
                                      "type": brk_t["id"], "color": brk_t["color"], "title": "Break"})
                if ba > 0:
                    win_e = min(DAY_END, ee + ba)
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
                    gap = next(((gs, ge) for gs, ge in _free_slots(all_obs, cursor, DAY_END)
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
                    cutoff = DAY_START
                movers = [a for a in self._all_acts
                          if a.get("date") == ds and a["startMin"] >= cutoff]
                if not movers:
                    return f"No blocks starting at or after {fmt_time(cutoff)} on {ds} to reflow."
                for a in movers:
                    dur = a["endMin"] - a["startMin"]
                    ns  = max(DAY_START, min(a["startMin"] + delay, DAY_END - dur))
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
        color = C_ERR_TXT.name() if error else C_MUTED.name()
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
        lines = [f"Daily Scheduler v{APP_VERSION}"]
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
        t = UpdateCheckThread()                       # unparented; ref held below
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
        p.setBrush(C_ACCENT); p.setPen(Qt.NoPen)
        p.drawRoundedRect(6, 6, 52, 52, 14, 14)
        p.setBrush(C_ON_ACCENT)
        p.drawRoundedRect(16, 14, 32, 6, 2, 2)        # calendar top bar
        p.setFont(QFont("Segoe UI", 20, QFont.Bold)); p.setPen(C_ON_ACCENT)
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
        self._tray.setToolTip(f"Daily Scheduler v{APP_VERSION}")
        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()};
                     border: 1px solid {C_BORDER2.name()}; padding: 4px; }}
            QMenu::item {{ padding: 6px 16px; border-radius: {RAD}px; }}
            QMenu::item:selected {{ background: {C_SURF2.name()}; }}
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
            self._manual_undo.clear()   # stack after restore is meaningless
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
        `kind` is start | end | test — drives popup badge/color."""
        if self._settings.get("notify_sound", True):
            self._play_alert_sound()
        if self._dnd_override:
            self._show_alert_popup(title, body, kind=kind, play_sound=False)
        elif self._tray:
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
                fire_at = sm - lead          # alert this many minutes before the block starts
                if now_min - self.NOTIFY_WINDOW <= fire_at <= now_min:
                    self._notified.add(key)   # this process won't re-check this block
                    # Fire exactly once even if another instance is also running: only
                    # the process that wins the atomic claim shows the alert.
                    if claim_block_alert(today, b["id"], sm):
                        when = f"Starting in {lead} min · " if lead else "Starting now · "
                        self._alert(
                            f"▶ {b['title']}",
                            f"{when}{fmt_time(b['startMin'])} – {fmt_time(b['endMin'])}")

        # End-of-block chime — opt-in only (default off). Same cross-process claim as starts.
        if self._settings.get("notify_end_chime", False):
            for b in self._all_acts:
                if b.get("date") != today:
                    continue
                em = b["endMin"]
                ekey = (b["id"], em)
                if ekey in self._notified_ends:
                    continue
                if now_min - self.NOTIFY_WINDOW <= em <= now_min:
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

# ── Entry point ────────────────────────────────────────────────────────────
def main():
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"Daily Scheduler {APP_VERSION}")
        return

    # Persist crashes to ~/.daily-scheduler/{app,crash}.log before anything else can fail.
    install_crash_logging()

    # Apply the saved theme before any widget (or the palette below) bakes colours.
    apply_theme(load_settings().get("theme", DEFAULT_THEME))

    # Register an explicit AppUserModelID so Windows shows our tray toasts as banners
    # (without this, Qt balloon notifications are silently dropped into the action center).
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "DailyScheduler.Planner.1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Daily Scheduler")
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    # Keep running in the tray after the window is closed, so reminders still fire.
    app.setQuitOnLastWindowClosed(False)

    pal = app.palette()
    pal.setColor(QPalette.Window,          C_BG)
    pal.setColor(QPalette.WindowText,      C_TEXT)
    pal.setColor(QPalette.Base,            C_SURF2)
    pal.setColor(QPalette.AlternateBase,   C_SURFACE)
    pal.setColor(QPalette.Text,            C_TEXT)
    pal.setColor(QPalette.Button,          C_SURFACE)
    pal.setColor(QPalette.ButtonText,      C_TEXT)
    pal.setColor(QPalette.Highlight,       C_ACCENT)
    pal.setColor(QPalette.HighlightedText, C_ON_ACCENT)
    pal.setColor(QPalette.ToolTipBase,     C_SURF2)
    pal.setColor(QPalette.ToolTipText,     C_TEXT)
    app.setPalette(pal)
    app.setStyleSheet(app_chrome_stylesheet())

    # ── Single instance ──────────────────────────────────────────────────────
    # Detect a running copy with an ATOMIC shared-memory create — race-safe at boot,
    # where Windows can launch several copies at once (an earlier listen()-based guard
    # let racing copies survive as zombies). Exactly one create() succeeds; every other
    # launch exits after pinging the winner to surface its window. A QLocalServer carries
    # that "show" ping. Wrapped so it can never block launch. On Windows the OS frees the
    # segment when the process ends, so a crash leaves no stale lock.
    try:
        _instance_user = getpass.getuser()   # portable (USERNAME on Win, USER/pwd on Linux)
    except Exception:
        _instance_user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    _key = "DailyScheduler.instance." + _instance_user
    _guard, _server = None, None
    try:
        _guard = QSharedMemory(_key)
        if _guard.create(1):
            QLocalServer.removeServer(_key)
            _server = QLocalServer()
            _server.listen(_key)
        else:
            # Another copy already holds the lock (or just won the race) — surface it, exit.
            _ping = QLocalSocket()
            _ping.connectToServer(_key)
            if _ping.waitForConnected(400):
                _ping.write(b"show"); _ping.flush(); _ping.waitForBytesWritten(400)
                _ping.disconnectFromServer()
            try: _guard.detach()      # release the failed-create handle right away
            except Exception: pass
            return
    except Exception:
        _guard, _server = None, None   # never let the guard stop the app from launching

    # Startup diagnostic: one line per SURVIVING launch (duplicates return above and
    # never reach here). If a post-boot read of this log shows two lines with the same
    # boot timestamp, a second instance slipped past the guard. No schedule data logged.
    try:
        with open(DATA_DIR / "startup.log", "a", encoding="utf-8") as _lf:
            _lf.write(f"{datetime.now().isoformat()} pid={os.getpid()} "
                      f"argv={sys.argv[1:]} guard={'won' if _server is not None else 'fell-through'}\n")
    except Exception:
        pass

    # The window is built via _build_window() — immediately for a normal launch, but
    # DEFERRED at Windows sign-in (see the startup-delay block below).
    holder = {"win": None}

    def _build_window():
        if holder["win"] is not None:
            return
        win = MainWindow()
        holder["win"] = win
        # Centre on the primary screen, then show. We always show the window (a
        # hidden-to-tray start was unreachable when the tray icon failed to render at
        # boot); it still lives in the tray after you close it (see closeEvent), and
        # _setup_tray() retries/re-asserts so that icon is reliable.
        geo = app.primaryScreen().availableGeometry()
        win.move((geo.width() - win.width()) // 2, (geo.height() - win.height()) // 2)
        win.show(); win.raise_(); win.activateWindow()

    # A second launch pings our server → surface the window (building it first if we're
    # still inside the startup delay and it doesn't exist yet).
    if _server is not None:
        def _surface():
            conn = _server.nextPendingConnection()
            if conn is not None:
                conn.close()
            _build_window()
            holder["win"]._show_from_tray()
        _server.newConnection.connect(_surface)

    # At Windows sign-in (--startup) the GPU driver can crash/reset for the first ~minute
    # (observed on this machine: AMD atiadlxx.dll fault + a GPU watchdog TDR right at boot).
    # A window built during that window can't paint, leaving the app in Task Manager with
    # no visible window. So DELAY building the window until the GPU has settled. The real
    # fix is disabling Windows Fast Startup / a GPU driver reinstall — this is a safety net.
    # startup_delay_sec (settings.json, default 60) is configurable; 0 disables the delay.
    if "--startup" in sys.argv:
        delay = max(0, int(load_settings().get("startup_delay_sec", 60) or 0))
        if delay:
            QTimer.singleShot(delay * 1000, _build_window)
        else:
            _build_window()
    else:
        _build_window()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
