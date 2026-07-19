"""Daily Scheduler — calendar view widgets (day/week/month/year, sidebar).

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

import calendar as _cal
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame,
    QSizePolicy,
    QMenu, QGridLayout, QProgressBar, QSplitter,
)
from PySide6.QtCore import (
    Qt, Signal, QRect,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QFontMetrics,
)

import core
import theme
from core import (
    ACTIVITY_TYPES,
    GUTTER_W,
    _free_slots,
    allday_cal_events,
    assign_overlap_cols,
    fmt_dur,
    fmt_time,
    min_to_y,
    timed_cal_events,
    y_to_min,
)
from theme import (
    BLOCK_FILL_A,
    _splitter_qss,
    block_colors,
    paint_schedule_block,
    style_activity_type_chip,
)


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
        self.setFixedHeight(min_to_y(core.DAY_END) + 24)

    def _snap(self, minute: int) -> int:
        m = round(minute / self.SNAP) * self.SNAP
        return max(core.DAY_START, min(core.DAY_END, m))

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
        p.fillRect(self.rect(), theme.C_BG)
        self._draw_grid(p)
        self._draw_free(p)
        self._draw_events(p)
        self._draw_drag(p)
        self._draw_now(p)

    def _draw_grid(self, p: QPainter):
        lbl_font = QFont("Segoe UI", 8)
        p.setFont(lbl_font)
        for h in range(core.DAY_START_H, core.DAY_END_H + 1):
            y = min_to_y(h * 60)
            p.setPen(QPen(theme.C_BORDER, 1))
            p.drawLine(GUTTER_W, y, self.width(), y)
            if h < core.DAY_END_H:
                yh = min_to_y(h * 60 + 30)
                pen = QPen(theme.C_GRID, 1, Qt.DashLine)
                p.setPen(pen)
                p.drawLine(GUTTER_W, yh, self.width(), yh)
            lbl = f"{h:02d}:00"
            p.setPen(theme.C_MUTED)
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
            fill = QColor(theme.C_ACCENT); fill.setAlpha(18)
            p.setPen(Qt.NoPen); p.setBrush(fill)
            p.drawRect(rect)
            pen = QPen(theme.C_ACCENT, 1, Qt.DashLine)
            pen.setColor(QColor(theme.C_ACCENT.red(), theme.C_ACCENT.green(), theme.C_ACCENT.blue(), 100))
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawRect(rect.adjusted(0, 0, -1, -1))
            if dur >= 20:
                p.setPen(QColor(theme.C_ACCENT.red(), theme.C_ACCENT.green(), theme.C_ACCENT.blue(), 180))
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
        fill = QColor(theme.C_ACCENT); fill.setAlpha(70)
        p.setPen(Qt.NoPen); p.setBrush(fill)
        p.drawRect(rect)
        p.setPen(QPen(theme.C_ACCENT, 1.5)); p.setBrush(Qt.NoBrush)
        p.drawRect(rect.adjusted(0, 0, -1, -1))
        p.setPen(theme.C_TEXT)
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
            c, bg = block_colors(blk.get("color") or theme.C_ACCENT.name())
            rr   = max(4, min(theme.RAD + 2, rect.height() // 2, 10))
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
        if not (core.DAY_START <= nm <= core.DAY_END):
            return
        y = min_to_y(nm)
        p.setPen(Qt.NoPen); p.setBrush(theme.C_NOW)
        p.drawEllipse(GUTTER_W - 5, y - 4, 9, 9)
        p.setPen(QPen(theme.C_NOW, 2)); p.setBrush(Qt.NoBrush)
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
                ns = max(core.DAY_START, min(ns, core.DAY_END - dur))
                self._preview = (self._edit_id, ns, ns + dur)
            elif self._edit_mode == "resize_top":
                ns = self._snap(os_ + delta)
                ns = max(core.DAY_START, min(ns, oe - self.SNAP))
                self._preview = (self._edit_id, ns, oe)
            else:  # resize_bottom
                ne = self._snap(oe + delta)
                ne = min(core.DAY_END, max(ne, os_ + self.SNAP))
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
            end = min(s + 60, core.DAY_END)
            for os_, _oe in occ:
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
            QMenu {{ background: {theme.C_SURFACE.name()}; color: {theme.C_TEXT.name()};
                     border: 1px solid {theme.C_BORDER2.name()}; padding: 4px; }}
            QMenu::item {{ padding: 6px 14px; border-radius: {theme.RAD}px; }}
            QMenu::item:selected {{ background: {theme.C_SURF2.name()}; }}
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
            QWidget {{ background: {theme.C_SURFACE.name()}; }}
            QLabel  {{ background: transparent; color: {theme.C_TEXT.name()}; }}
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
            f"font-size: 9px; font-weight: bold; letter-spacing: 1px; color: {theme.C_MUTED.name()};")
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
        hint.setStyleSheet(f"color: {theme.C_MUTED.name()}; font-size: 10px;")
        al.addWidget(hint)
        self._split.addWidget(add_sec)

        # ── Summary — tight stack like the original (no stretched gaps) ────
        sum_sec = QWidget()
        sum_sec.setMinimumHeight(80)
        sl = QVBoxLayout(sum_sec)
        sl.setContentsMargins(12, 12, 12, 8); sl.setSpacing(6)

        sh = QLabel("TODAY'S SUMMARY")
        sh.setStyleSheet(
            f"font-size: 9px; font-weight: bold; letter-spacing: 1px; color: {theme.C_MUTED.name()};")
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
        DAY_T = core.DAY_END - core.DAY_START
        totals: Dict[str, int] = {}
        for b in all_b:
            totals[b["type"]] = totals.get(b["type"], 0) + (b["endMin"] - b["startMin"])

        cats = [
            {"id": "calendar", "label": "Meetings", "color": theme.C_INFO.name()},
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
            lbl = QLabel(cat["label"]); lbl.setStyleSheet(f"color: {theme.C_MUTED.name()}; font-size: 11px;")
            val = QLabel(fmt_dur(mins)); val.setStyleSheet(f"color: {theme.C_TEXT.name()}; font-size: 11px; font-weight: bold;")
            top.addWidget(dot); top.addWidget(lbl, 1); top.addWidget(val)
            rl.addLayout(top)

            bar = QProgressBar()
            bar.setFixedHeight(3)
            bar.setTextVisible(False)
            bar.setRange(0, DAY_T)
            bar.setValue(mins)
            bar.setStyleSheet(f"""
                QProgressBar {{ background: {theme.C_BORDER.name()}; border-radius: {theme.RAD}px; border: none; }}
                QProgressBar::chunk {{ background: {cat['color']}; border-radius: {theme.RAD}px; }}
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
        return int(top + (minutes - core.DAY_START) / (core.DAY_END - core.DAY_START) * span)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), theme.C_BG)
        self._block_hits = []
        self._hdr_hits   = []

        days  = self.days()
        top_h = self._top_h()
        any_ad = top_h > self.HDR_H
        cw    = (self.width() - self.GUT_W) / 7.0
        today = date.today()

        # hour grid + gutter labels (every 2 h — one hour is ~30 px here)
        p.setFont(QFont("Segoe UI", 7))
        for h in range(core.DAY_START_H, core.DAY_END_H + 1):
            y = self._y(h * 60)
            p.setPen(QPen(theme.C_GRID if h % 2 else theme.C_BORDER, 1))
            p.drawLine(self.GUT_W, y, self.width(), y)
            if h % 2 == 0 and h < core.DAY_END_H:
                p.setPen(theme.C_MUTED)
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
            p.setPen(QPen(theme.C_BORDER, 1))
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
                c, bg = block_colors(b.get("color") or theme.C_ACCENT.name())
                rr = max(3, min(theme.RAD, rect.height() // 2, 8))
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
                if core.DAY_START <= nm <= core.DAY_END:
                    ny = self._y(nm)
                    p.setPen(QPen(theme.C_NOW, 2))
                    p.drawLine(x0 + 1, ny, x1, ny)
                    p.setPen(Qt.NoPen); p.setBrush(theme.C_NOW)
                    p.drawEllipse(x0 - 3, ny - 3, 7, 7)

            # header last, on top — click target for "open this day"
            hdr = QRect(x0, 0, int(cw), top_h)
            self._hdr_hits.append((hdr, d))
            p.setBrush(theme.C_SURFACE); p.setPen(Qt.NoPen)
            p.drawRect(hdr)
            p.setPen(QPen(theme.C_BORDER, 1))
            p.drawLine(x0, top_h, x1, top_h)
            if i:
                p.drawLine(x0, 0, x0, top_h)
            name_rect = QRect(x0, 0, int(cw), self.HDR_H)
            lbl = d.strftime("%a %d")
            if d == today:
                p.setPen(Qt.NoPen); p.setBrush(theme.C_ACCENT)
                w = QFontMetrics(fn_hdr_d).horizontalAdvance(lbl) + 16
                p.drawRoundedRect(QRect(name_rect.center().x() - w // 2, 7, w, 20), theme.RAD, theme.RAD)
                p.setPen(theme.C_ON_ACCENT)
            else:
                p.setPen(theme.C_TEXT)
            p.setFont(fn_hdr_d)
            p.drawText(name_rect, Qt.AlignCenter, lbl)

            # all-day strip under the day name (holidays, due dates, spirit week)
            if any_ad:
                ads = allday_cal_events(self._cal.get(ds, []))
                ad_rect = QRect(x0 + 2, self.HDR_H, int(cw) - 4, self.AD_H - 2)
                if ads:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(theme.C_INFO.red(), theme.C_INFO.green(), theme.C_INFO.blue(), 40))
                    p.drawRoundedRect(ad_rect, theme.RAD, theme.RAD)
                    p.setPen(theme.C_INFO); p.setFont(fn_tiny)
                    text = " · ".join(e.get("title", "") for e in ads)
                    p.drawText(ad_rect.adjusted(4, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm_tiny.elidedText(text, Qt.ElideRight, ad_rect.width() - 8))

        # gutter/header corner + outer frame line under the header row
        p.setPen(QPen(theme.C_BORDER, 1))
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
        p.fillRect(self.rect(), theme.C_BG)
        self._hits = []

        weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(self._year, self._month)
        cw = self.width() / 7.0
        ch = (self.height() - self.HDR_H) / len(weeks)
        today = date.today()

        p.setFont(QFont("Segoe UI", 8, QFont.Bold))
        p.setPen(theme.C_MUTED)
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
                p.setPen(QPen(theme.C_BORDER, 1))
                p.drawRect(cell)

                in_month = (d.month == self._month)
                if d == today:
                    p.setBrush(theme.C_ACCENT); p.setPen(Qt.NoPen)
                    p.drawEllipse(QRect(x + 5, y + 3, 20, 20))
                    p.setPen(theme.C_ON_ACCENT)
                else:
                    p.setPen(theme.C_TEXT if in_month else theme.C_GHOST)
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
                    col, bg = block_colors(ev.get("color") or theme.C_ACCENT.name())
                    if not in_month:
                        col = QColor(col.red(), col.green(), col.blue(), 120)
                        bg  = QColor(bg.red(), bg.green(), bg.blue(), max(28, BLOCK_FILL_A // 2))
                    p.setPen(Qt.NoPen); p.setBrush(bg)
                    p.drawRoundedRect(chip, 4, 4)
                    p.setPen(col)
                    label = f"{fmt_time(ev.get('startMin', 0))} {ev.get('title', '')}"
                    p.drawText(chip.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm_chip.elidedText(label, Qt.ElideRight, chip.width() - 8))
                if len(evs) > len(shown):
                    p.setPen(theme.C_MUTED)
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
        p.fillRect(self.rect(), theme.C_BG)
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

            p.setFont(fn_title); p.setPen(theme.C_ACCENT)
            p.drawText(QRect(int(ox), int(oy), int(mw) - 28, 18),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       date(self._year, m, 1).strftime("%B"))

            cw  = (mw - 28) / 7.0
            chh = (mh - 50) / 7.0
            rad = max(3, int(min(cw, chh) / 2) - 1)

            p.setFont(fn_hdr); p.setPen(theme.C_MUTED)
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
                        p.setBrush(theme.C_ACCENT); p.setPen(Qt.NoPen)
                        p.drawEllipse(cell.center(), rad, rad)
                        p.setPen(theme.C_ON_ACCENT)
                    elif d.isoformat() in self._busy:
                        bg = QColor(theme.C_ACCENT); bg.setAlpha(55)
                        p.setBrush(bg); p.setPen(Qt.NoPen)
                        p.drawEllipse(cell.center(), rad, rad)
                        p.setPen(theme.C_TEXT)
                    else:
                        p.setPen(theme.C_MUTED)
                    p.setBrush(Qt.NoBrush)
                    p.drawText(cell, Qt.AlignCenter, str(d.day))

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        for rect, d in self._hits:
            if rect.contains(pos):
                self.day_clicked.emit(d)
                return
