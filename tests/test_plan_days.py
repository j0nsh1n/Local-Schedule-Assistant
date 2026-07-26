"""v4.5.0 — multi-day planning safety (Mistral week-plan failure mode).

Synthetic data only. Covers:
  * expand_date_targets resolves start_date+end_date (Month/Day) correctly
    even when the viewed day is elsewhere
  * weekdays alone still anchors on the viewed day (documented behaviour)
  * plan_days with exact blocks applies to every day in the range
  * plan_days keeps fixed anchor times (workout 16:00, dinner 18:30)
  * add_recurring with start_date+end_date lands on the named range
  * add_recurring without a range reports the viewed-day anchor
  * plan_days is registered in AI_TOOLS
"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai
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

# Viewed day is Aug 2 — the failure mode where add_recurring(daily) started HERE
# instead of the user-named Jul 27–Aug 2 window.
VIEW = date(2026, 8, 2)

print("── expand_date_targets ──")
days, err = core.expand_date_targets(
    VIEW, start_date="7/27", end_date="8/2")
check("no error for 7/27–8/2", err is None)
check("7 days inclusive", len(days) == 7)
check("starts 2026-07-27", days[0] == "2026-07-27")
check("ends 2026-08-02", days[-1] == "2026-08-02")
check("does NOT start at viewed day Aug 2", days[0] != "2026-08-02" or len(days) == 1)

days2, err2 = core.expand_date_targets(VIEW, weekdays=["daily"], weeks=1)
check("weekdays daily from viewed day", days2[0] == "2026-08-02" and len(days2) == 7)
check("weekdays ends Aug 8", days2[-1] == "2026-08-08")

days3, err3 = core.expand_date_targets(
    VIEW, dates=["7/27", "7/28", "7/29"])
check("explicit dates list", days3 == ["2026-07-27", "2026-07-28", "2026-07-29"])

_, err4 = core.expand_date_targets(VIEW)
check("missing selector errors", err4 is not None)

_, err5 = core.expand_date_targets(VIEW, start_date="8/2", end_date="7/27")
check("reversed range errors", err5 is not None and "before" in err5)

print("── plan_days (exact blocks) ──")
mw = mainwindow.MainWindow()
mw._cur_date = VIEW
mw._all_acts = []
mw._cal_by_date = {}

blocks = [
    {"title": "College Essays", "start": "08:00", "end": "09:30", "type": "assignments"},
    {"title": "Break", "start": "09:30", "end": "10:00", "type": "free"},
    {"title": "SAT Prep", "start": "10:00", "end": "11:00", "type": "study"},
    {"title": "Lunch", "start": "12:00", "end": "13:00", "type": "meals"},
    {"title": "Workout", "start": "16:00", "end": "17:00", "type": "exercise"},
    {"title": "Dinner", "start": "18:30", "end": "19:30", "type": "meals"},
]
res = mw._ai_execute("plan_days", {
    "start_date": "7/27", "end_date": "8/2", "blocks": blocks,
})
check("tool result mentions 7 days", "7 day" in res or "7 day(s)" in res)
check("tool result lists Jul 27", "2026-07-27" in res)
check("tool result lists Aug 2", "2026-08-02" in res)
# Must NOT have spilled into Aug 3–8 (the old add_recurring failure)
check("no spill on Aug 3", not any(a["date"] == "2026-08-03" for a in mw._all_acts))
check("no spill on Aug 8", not any(a["date"] == "2026-08-08" for a in mw._all_acts))

by_day = {}
for a in mw._all_acts:
    by_day.setdefault(a["date"], []).append(a)
check("all 7 target days have blocks", len(by_day) == 7)
sample = sorted(by_day["2026-07-28"], key=lambda a: a["startMin"])
titles = [a["title"] for a in sample]
check("workout kept at 16:00",
      any(a["title"] == "Workout" and a["startMin"] == 960 and a["endMin"] == 1020
          for a in sample))
check("dinner kept at 18:30",
      any(a["title"] == "Dinner" and a["startMin"] == 1110 and a["endMin"] == 1170
          for a in sample))
check("lunch at 12:00",
      any(a["title"] == "Lunch" and a["startMin"] == 720 for a in sample))
check("each day got 6 blocks", all(len(v) == 6 for v in by_day.values()))

print("── add_recurring date range ──")
mw2 = mainwindow.MainWindow()
mw2._cur_date = VIEW
mw2._all_acts = []
res2 = mw2._ai_execute("add_recurring", {
    "title": "Study", "start": "16:00", "end": "18:00", "type": "study",
    "start_date": "7/27", "end_date": "8/2", "weekdays": ["daily"],
})
dates2 = sorted({a["date"] for a in mw2._all_acts})
check("add_recurring range is Jul 27–Aug 2",
      dates2[0] == "2026-07-27" and dates2[-1] == "2026-08-02" and len(dates2) == 7)
check("result lists real dates", "2026-07-27" in res2 and "2026-08-02" in res2)

res3 = mw2._ai_execute("add_recurring", {
    "title": "X", "start": "09:00", "end": "10:00", "type": "study",
    "weekdays": ["daily"], "weeks": 1,
})
check("default-anchor warning when no range", "viewed day" in res3.lower() or "2026-08-02" in res3)

print("── registry ──")
names = {t["function"]["name"] for t in ai.AI_TOOLS}
check("plan_days registered", "plan_days" in names)
check("add_recurring still registered", "add_recurring" in names)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
