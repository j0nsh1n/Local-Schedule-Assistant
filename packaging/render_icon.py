#!/usr/bin/env python3
"""Render the Daily Scheduler app icon to a PNG (for AppImage / desktop).

Matches MainWindow._make_app_icon() (nocturne accent ◈ calendar tile), at a
size suitable for hicolor icons. Offscreen-safe — no window is shown.

Usage:
  QT_QPA_PLATFORM=offscreen python packaging/render_icon.py out.png [size]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Prefer offscreen when no display (CI / headless packaging).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication


def render(path: Path, size: int = 256) -> None:
    app = QApplication.instance() or QApplication([])
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    accent = QColor("#e8b84a")
    on_accent = QColor("#0b0b0d")
    margin = max(1, size // 10)
    tile = size - 2 * margin
    radius = max(2, size * 14 // 64)

    p.setBrush(accent)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(margin, margin, tile, tile, radius, radius)

    # Calendar top bar (same proportions as the 64px tray icon).
    bar_m = size * 16 // 64
    bar_h = max(2, size * 6 // 64)
    bar_r = max(1, size * 2 // 64)
    p.setBrush(on_accent)
    p.drawRoundedRect(bar_m, bar_m - size * 2 // 64, size - 2 * bar_m, bar_h, bar_r, bar_r)

    font_px = max(10, size * 20 // 64)
    p.setFont(QFont("Sans Serif", font_px, QFont.Bold))
    p.setPen(on_accent)
    p.drawText(QRect(0, size * 14 // 64, size, size - size * 14 // 64), Qt.AlignCenter, "◈")
    p.end()

    path.parent.mkdir(parents=True, exist_ok=True)
    if not pm.save(str(path), "PNG"):
        raise SystemExit(f"failed to write {path}")
    print(f"Wrote {path} ({size}x{size})")
    # Keep QApplication alive only for this process; no event loop needed.
    del app


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "daily-scheduler.png")
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 256
    render(out, size)


if __name__ == "__main__":
    main()
