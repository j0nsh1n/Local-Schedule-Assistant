"""v3.3.0 — live Now/Next countdown (roadmap #3). Offscreen, synthetic data only
(DATA_FILE/SETTINGS_FILE/CREDS_FILE redirected to a temp dir — never the real store).
Covers the pure now_next_summary() at every day-position and the MainWindow wiring
(status-bar label + composed tray tooltip, always computed for TODAY)."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import mainwindow
from PySide6.QtWidgets import QApplication

TMP = Path(tempfile.mkdtemp())
core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.CREDS_FILE    = TMP / "credentials.json"
core.TOKEN_FILE    = TMP / "token.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)

def blk(sm, em, title):
    return {"id": f"b{sm}", "date": "2026-07-06", "startMin": sm, "endMin": em,
            "title": title, "type": "study", "color": "#888"}

# 09:00–10:00 Math, 10:00–10:30 Break, 13:00–14:00 Lunch
day = [blk(540, 600, "Math"), blk(600, 630, "Break"), blk(780, 840, "Lunch")]

# ── Pure now_next_summary ────────────────────────────────────────────────────
# inside Math at 09:20 → 40m left, next is Break at 10:00
s = core.now_next_summary(day, 560)
check("in-block shows now + minutes left", s == "Now: Math · 40m left  →  Next: Break at 10:00")

# exactly at a boundary 10:00 → Break is current, Lunch is next
s = core.now_next_summary(day, 600)
check("boundary: new block current", s.startswith("Now: Break · 30m left"))
check("boundary: next is Lunch", s.endswith("Next: Lunch at 13:00"))

# in the gap 10:30–13:00 at 11:00 → no current, next Lunch in 2h
s = core.now_next_summary(day, 660)
check("gap shows next-with-eta only", s == "Next: Lunch at 13:00 (in 2h)")

# before the first block at 08:00 → next Math in 1h
s = core.now_next_summary(day, 480)
check("pre-day shows first block", s == "Next: Math at 09:00 (in 1h)")

# a block overlapping the current one is NOT labeled Next (user blocks + calendar
# events mix, so overlaps happen); Next must start at/after the current block ends.
overlap = [blk(540, 600, "Study"), blk(570, 630, "Meeting"), blk(650, 700, "Gym")]
s = core.now_next_summary(overlap, 560)   # in Study; Meeting overlaps it, Gym follows
check("overlap not labeled Next", s == "Now: Study · 40m left  →  Next: Gym at 10:50")

# after the last block at 15:00 → empty
check("after last block → empty", core.now_next_summary(day, 900) == "")
# empty schedule → empty
check("empty day → empty", core.now_next_summary([], 600) == "")

# long title is elided to 30 chars
longt = blk(600, 660, "A really really long block title that overflows")
s = core.now_next_summary([longt], 620)
check("long title elided", "…" in s and "Now: A really really long block" in s)

# hh:mm formatting for an afternoon start
s = core.now_next_summary([blk(875, 900, "Gym")], 800)
check("afternoon time formats 24h", "at 14:35" in s)

# ── MainWindow wiring ────────────────────────────────────────────────────────
mw = mainwindow.MainWindow()
# put the blocks on TODAY so _nownext_text (which uses date.today) sees them
today = date.today().isoformat()
mw._all_acts = [{**b, "date": today} for b in day]
now_min = datetime.now().hour * 60 + datetime.now().minute
expect = core.now_next_summary(mw._all_acts, now_min)
mw._update_nownext()
check("status label matches pure summary for today", mw._nownext_lbl.text() == expect)

# tray tooltip composes version + now/next (+ update line when present)
class FakeTray:
    def __init__(self): self.tip = ""
    def setToolTip(self, t): self.tip = t
mw._tray = FakeTray()
mw._refresh_tray_tooltip()
check("tooltip has version line", mw._tray.tip.splitlines()[0] == f"Daily Scheduler v{core.APP_VERSION}")
if expect:
    check("tooltip includes now/next", expect in mw._tray.tip)
else:
    check("tooltip has no now/next when idle", core.APP_VERSION in mw._tray.tip)
mw._update_tag = "v9.9.9"
mw._refresh_tray_tooltip()
check("tooltip appends update line", "Update to v9.9.9 available" in mw._tray.tip)

# viewing a different day must NOT change what Now/Next reports (always today)
mw._cur_date = date(2020, 1, 1)
mw._update_nownext()
check("now/next ignores the viewed day", mw._nownext_lbl.text() == expect)

n = len(results)
print(f"RESULT: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{n})")
sys.exit(0 if all(results) else 1)
