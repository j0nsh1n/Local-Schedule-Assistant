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

print(f"\n{sum(results)}/{len(results)} passed")
print("RESULT:", "PASS" if all(results) else "FAIL")
sys.exit(0 if all(results) else 1)
