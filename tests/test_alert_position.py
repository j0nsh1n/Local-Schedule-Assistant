"""Alert popup corner geometry (no display required for pure math)."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect
from dialogs import alert_corner_top_left

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

# Single 1920×1080 screen at origin
g = QRect(0, 0, 1920, 1080)
x, y = alert_corner_top_left(g, 380, 120, margin=16, stack_idx=0)
check("bottom-right x", x == 1920 - 380 - 16)
check("bottom-right y", y == 1080 - 120 - 16)

x1, y1 = alert_corner_top_left(g, 380, 120, margin=16, stack_idx=1, gap=8)
check("stack moves up by height+gap", y1 == y - (120 + 8))
check("stack keeps same x", x1 == x)

# Dual monitor: primary on the RIGHT at x=2560 (this machine's layout)
g2 = QRect(2560, 0, 2560, 1440)
x2, y2 = alert_corner_top_left(g2, 380, 110, margin=16, stack_idx=0)
check("dual primary uses geo.x not 0", x2 == 2560 + 2560 - 380 - 16)
check("not left of primary", x2 >= 2560)
check("y on primary", y2 == 1440 - 110 - 16)

# Secondary on the left
g3 = QRect(0, 0, 2560, 1440)
x3, y3 = alert_corner_top_left(g3, 380, 110, margin=16, stack_idx=0)
check("secondary corner x", x3 == 2560 - 380 - 16)
check("secondary not on primary", x3 < 2560)


# ── Window TYPE per platform (regression: v4.6.2 shipped Qt.Window everywhere)
# On Windows/macOS the toast must be a Qt.Tool so WS_EX_TOOLWINDOW keeps it out
# of the taskbar and Alt-Tab — a frameless card that auto-dismisses after 12 s
# must not leave a ghost taskbar button. On Linux/KWin Qt.Tool maps to "Utility",
# where placement/keep-above rules are ignored and the toast gets centred, so
# there it must be a plain Qt.Window. Offscreen/Linux testing cannot see the
# Windows half of this, hence the explicit per-platform assertion.
import dialogs
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

qapp = QApplication.instance() or QApplication(sys.argv)


class _FakePlatform:
    def __init__(self, name): self._name = name
    def system(self): return self._name


_real_platform = dialogs.platform

def _window_type(osname):
    dialogs.platform = _FakePlatform(osname)
    try:
        w = dialogs.AlertPopup("t", "b", QIcon(), kind="start")
        wt = w.windowFlags() & Qt.WindowType_Mask
        w.deleteLater()
        return wt
    finally:
        dialogs.platform = _real_platform

for osname in ("Windows", "Darwin"):
    wt = _window_type(osname)
    check(f"{osname}: is a Qt.Tool (stays out of the taskbar / Alt-Tab)",
          wt == Qt.Tool)

check("Linux: is a plain Qt.Window (KWin 'Utility' breaks placement)",
      _window_type("Linux") == Qt.Window)

# Flags that must hold everywhere.
for osname in ("Windows", "Linux"):
    dialogs.platform = _FakePlatform(osname)
    try:
        w = dialogs.AlertPopup("t", "b", QIcon(), kind="start")
        f = w.windowFlags()
        check(f"{osname}: frameless", bool(f & Qt.FramelessWindowHint))
        check(f"{osname}: stays on top", bool(f & Qt.WindowStaysOnTopHint))
        check(f"{osname}: never steals focus",
              w.testAttribute(Qt.WA_ShowWithoutActivating))
        w.deleteLater()
    finally:
        dialogs.platform = _real_platform


print(f"\n{sum(results)}/{len(results)} passed")
print("RESULT:", "PASS" if all(results) else "FAIL")
sys.exit(0 if all(results) else 1)
