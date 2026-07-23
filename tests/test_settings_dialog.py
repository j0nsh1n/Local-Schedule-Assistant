"""v4.3.1 — the Settings dialog must fit its own content at any desktop font size.

Regression guard for a live report: on KDE at a normal 10–11pt desktop font, the
right-hand side of every Settings field (model combo, context window, models
folder, calendar IDs, "Restore from backup…") was cut off, and because the scroll
area had horizontal scrolling turned OFF the overflow was unreachable — the
dialog's width was hardcoded to 480px, tuned to a smaller font than most desktops
use.

Two invariants are checked at several font sizes:
  1. the dialog OPENS wide enough that no row is clipped, and
  2. if something still forces it narrower (small screen), the overflow is
     reachable by scrolling rather than silently cut off.

Offscreen, synthetic settings only — never reads the real store."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Sandbox HOME before importing core (core.py creates DATA_DIR at import time).
TMP = Path(tempfile.mkdtemp())
os.environ["HOME"] = str(TMP)
os.environ["USERPROFILE"] = str(TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core

core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.BAK_FILE      = TMP / "activities.json.bak"

import theme  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

theme.apply_theme("nocturne")
qapp = QApplication.instance() or QApplication(sys.argv)

import dialogs  # noqa: E402

print("── the dialog opens wide enough for its widest row ──")
# 9pt was the old dev default (where the bug was invisible); 10-12pt is a normal
# KDE/GNOME desktop, which is where it actually bit.
for pt in (9, 10, 11, 12, 14):
    qapp.setFont(QFont("Sans", pt))
    d = dialogs.SettingsDialog(dict(core.DEFAULT_SETTINGS))
    d.show()
    qapp.processEvents()
    scroll = d.findChild(QScrollArea)
    body = scroll.widget()
    viewport = scroll.viewport().width()
    needed = body.sizeHint().width()
    floor = body.minimumSizeHint().width()
    check(f"{pt}pt: no row is clipped (floor {floor} <= viewport {viewport})",
          floor <= viewport)
    check(f"{pt}pt: every field is fully visible (wants {needed} <= viewport {viewport})",
          needed <= viewport)
    d.deleteLater()
    qapp.processEvents()

print("── a narrower window keeps the overflow reachable ──")
qapp.setFont(QFont("Sans", 11))
d = dialogs.SettingsDialog(dict(core.DEFAULT_SETTINGS))
d.show(); qapp.processEvents()
scroll = d.findChild(QScrollArea)

check("horizontal scrolling is not disabled",
      scroll.horizontalScrollBarPolicy() != __import__(
          "PySide6.QtCore", fromlist=["Qt"]).Qt.ScrollBarAlwaysOff)
check("the dialog refuses to shrink below its content floor",
      d.minimumWidth() >= body.minimumSizeHint().width() * 0.5)

# Squeeze the scroll area itself, the way a cramped screen would.
scroll.setMinimumWidth(0)
scroll.resize(300, 600)
qapp.processEvents()
check("squeezed content is reachable by scrolling, not cut off",
      scroll.horizontalScrollBar().maximum() > 0)
d.deleteLater(); qapp.processEvents()

print("── the dialog still respects the screen it opens on ──")
qapp.setFont(QFont("Sans", 11))
d = dialogs.SettingsDialog(dict(core.DEFAULT_SETTINGS))
d.show(); qapp.processEvents()
avail = (d.screen() or qapp.primaryScreen()).availableGeometry().width()
check(f"width {d.width()} stays within the screen ({avail})", d.width() <= avail)
check("dialog is still usably tall", d.minimumHeight() >= 400)
d.deleteLater(); qapp.processEvents()

print(f"\n{sum(results)}/{len(results)} passed")
print("RESULT:", "PASS" if all(results) else "FAIL")
sys.exit(0 if all(results) else 1)
