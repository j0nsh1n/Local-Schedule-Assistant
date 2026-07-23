"""Daily Scheduler — theme registry, colour globals, QSS + paint helpers.

Contents
    THEMES ............... the registry (nocturne = dark, slate = light)
    C_* colour globals ... re-pointed by apply_theme(); see the rule below
    app_chrome_stylesheet  global QSS applied once at launch
    Block paint recipe ... block_colors / paint_schedule_block /
                           style_activity_type_chip — chips and timeline tiles
                           share these so a picked category looks like the
                           block that will land on the day

IMPORTANT — how to read a colour from another module:

    import theme;  theme.C_BG          # correct: follows apply_theme()
    from theme import C_BG             # WRONG: binds once at import time

apply_theme() rebinds the module globals, so a `from theme import C_BG` captures
whichever colour happened to be current when that module was first imported and
then silently stops updating. tests/test_module_boundaries.py fails the build if
anyone does this.

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""


from PySide6.QtWidgets import (
    QSizePolicy,
)
from PySide6.QtCore import (
    Qt, QRect,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen,
)

from core import DEFAULT_THEME


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
        "rad": 4, "rad_lg": 6,
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
        "rad": 4, "rad_lg": 6,
    },
}

# Chrome colour globals — (re)assigned by apply_theme(); initialised at import.
C_BG = C_SURFACE = C_SURF2 = C_BORDER = C_BORDER2 = None
C_TEXT = C_MUTED = C_ACCENT = C_ACCENT2 = C_ON_ACCENT = C_NOW = None
C_GRID = C_GHOST = C_OK = C_OK_TXT = C_ERR = C_ERR_TXT = C_WARN = C_INFO = None
RAD = RAD_LG = 0
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
    global RAD, RAD_LG, THEME_NAME
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
                         accent_w: int = 3, outline: bool = False):
    """Square planner tiles (deliberately not GCal-style rounded cards): solid fill,
    1px outline, crisp left accent bar."""
    p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), BLOCK_OUTLINE_A), 1))
    p.setBrush(fill)
    p.drawRect(rect.adjusted(0, 0, -1, -1))
    if accent_w > 0:
        p.fillRect(QRect(rect.x(), rect.y(), accent_w, rect.height()), accent)
    if outline:
        p.setPen(QPen(accent, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect.adjusted(1, 1, -2, -2))

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
