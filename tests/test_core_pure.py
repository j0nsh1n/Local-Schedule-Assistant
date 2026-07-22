"""v4.3.0 — the planning math, tested with NO Qt and NO display.

core.py has been Qt-free since the v4.2.0 split, so this suite imports it alone
and runs anywhere Python does — no PySide6, no offscreen platform, no window.
That makes it the fast feedback loop for the logic that has historically carried
the real bugs (overlap handling, calendar-aware placement, date resolution),
and it keeps core honest: if someone imports Qt into core.py, this stops running.

Synthetic data only — DATA_FILE and friends are redirected to a temp dir before
anything can touch the real store, and nothing here reads a schedule.
"""
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core

TMP = Path(tempfile.mkdtemp())
core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

assert "PySide6" not in sys.modules, "core pulled in Qt — this suite must stay Qt-free"


def blk(sm, em, title="t", d="2026-07-20"):
    return {"id": f"b{sm}-{em}", "date": d, "startMin": sm, "endMin": em,
            "type": "study", "color": "#888", "title": title}


print("── parse_hhmm / coerce_end_min ──")
check("parse_hhmm 24h", core.parse_hhmm("14:30") == 870)
check("parse_hhmm midnight", core.parse_hhmm("00:00") == 0)
check("parse_hhmm 24:00 is end of day", core.parse_hhmm("24:00") == 1440)
check("coerce_end_min keeps a valid end", core.coerce_end_min(600, 660) == 660)
# End=00:00 with a later start means "through midnight" (QTime can't hold 24:00).
check("coerce_end_min maps end 00:00 to end of day",
      core.coerce_end_min(1320, 0) == core.DAY_END)
check("coerce_end_min leaves 00:00–00:00 zero-length", core.coerce_end_min(0, 0) == 0)
check("coerce_end_min keeps a re-saved full day at 24:00",
      core.coerce_end_min(0, 0, original_end=core.DAY_END) == core.DAY_END)

print("── _merge / _free_slots ──")
check("_merge fuses overlapping intervals",
      core._merge([(0, 60), (30, 90)]) == [(0, 90)])
check("_merge keeps disjoint intervals",
      core._merge([(0, 60), (120, 180)]) == [(0, 60), (120, 180)])
free = core._free_slots([(600, 660)])
check("_free_slots brackets a single block",
      free[0] == (core.DAY_START, 600) and free[-1] == (660, core.DAY_END))
check("_free_slots on an empty day is the whole day",
      core._free_slots([]) == [(core.DAY_START, core.DAY_END)])

print("── find_free_placement ──")
day = [blk(600, 660)]
check("free slot is used as-is when open", core.find_free_placement(day, 700, 60) == 700)
check("occupied slot relocates to the nearest free minute",
      core.find_free_placement(day, 600, 60) in (540, 660))
check("no room at all returns None",
      core.find_free_placement([blk(0, 1440)], 600, 60) is None)
check("a request longer than any gap returns None",
      core.find_free_placement([blk(600, 660)], 600, 1440) is None)

print("── sequentialize (overlap + calendar awareness) ──")
kept, adj, dropped = core.sequentialize([blk(600, 660), blk(630, 690)])
check("overlapping blocks are pushed apart", kept[0]["endMin"] <= kept[1]["startMin"])
check("the push is reported as an adjustment", adj == 1 and dropped == 0)
check("durations survive the push",
      all(k["endMin"] - k["startMin"] == 60 for k in kept))

kept, adj, dropped = core.sequentialize([blk(600, 660)], blocked=[(600, 700)])
check("a block is pushed off a calendar window", kept[0]["startMin"] >= 700)
kept, adj, dropped = core.sequentialize([blk(600, 660)], blocked=[(600, 660), (660, 780)])
check("a block flows past back-to-back meetings", kept[0]["startMin"] >= 780)

kept, adj, dropped = core.sequentialize([blk(1380, 1440), blk(1380, 1440)])
check("a block with no room left in the day is dropped", dropped == 1)

kept, _, _ = core.sequentialize([blk(600, 660)], blocked=[(0, 60)])
check("an irrelevant calendar window leaves the block alone",
      kept[0]["startMin"] == 600)

print("── _earliest_fit ──")
check("_earliest_fit finds the cursor itself when free",
      core._earliest_fit([], 600, 60) == 600)
check("_earliest_fit flows past an anchor",
      core._earliest_fit([(600, 660)], 600, 60) == 660)
check("_earliest_fit returns None when it cannot fit",
      core._earliest_fit([(0, 1440)], 0, 60) is None)

print("── resolve_date (ignores a hallucinated year) ──")
base = date(2026, 7, 20)          # a Monday
check("ISO passes through", core.resolve_date("2026-07-22", base) == "2026-07-22")
check("today", core.resolve_date("today", base) == "2026-07-20")
check("tomorrow", core.resolve_date("tomorrow", base) == "2026-07-21")
check("Month/Day snaps to the nearest occurrence",
      core.resolve_date("7/22", base) == "2026-07-22")
check("a weekday word resolves forward",
      core.resolve_date("Thursday", base) == "2026-07-23")
check("garbage returns None", core.resolve_date("someday", base) is None)

print("── norm_title (fuzzy matching) ──")
check("emoji and case are stripped", core.norm_title("🏋 Gym Session") == "gym session")
check("punctuation is stripped", core.norm_title("Math -- HW!") == "math hw")

print("── now_next_summary ──")
blocks = [blk(600, 660, "Study"), blk(700, 760, "Gym")]
mid = core.now_next_summary(blocks, 630)
check("names the current block", "Study" in mid)
check("names what comes next", "Gym" in mid)
check("after the last block it says nothing",
      core.now_next_summary(blocks, 1400) == "")
gap = core.now_next_summary(blocks, 670)
check("between blocks it previews the next", "Gym" in gap and "Study" not in gap)

print("── assign_overlap_cols ──")
cols = core.assign_overlap_cols([blk(600, 660), blk(600, 660)])
check("two simultaneous blocks get different columns",
      cols[0]["_col"] != cols[1]["_col"])
check("both report a 2-column layout",
      cols[0]["_tcols"] == cols[1]["_tcols"] == 2)
cols = core.assign_overlap_cols([blk(600, 660), blk(700, 760)])
check("sequential blocks share a column",
      cols[0]["_col"] == cols[1]["_col"] == 0)
check("sequential blocks render full width",
      cols[0]["_tcols"] == cols[1]["_tcols"] == 1)

print("── week_ahead_lines (clock-free) ──")
cal = {"2026-07-20": [{"title": "Exam", "startMin": 600, "endMin": 660,
                       "allDay": False}]}
wk = core.week_ahead_lines(cal, date(2026, 7, 20), days=7)
check("an upcoming event is listed", "Exam" in wk)
check("empty days are omitted", wk.count("\n") <= 2)
check("nothing upcoming yields an empty string",
      core.week_ahead_lines({}, date(2026, 7, 20), days=7) == "")

print("── round-trip through storage (temp dir only) ──")
acts = [blk(600, 660, "synthetic")]
core.save_all_activities(acts)
check("save wrote to the redirected path", core.DATA_FILE.exists())
check("load returns what was saved",
      [a["title"] for a in core.load_all_activities()] == ["synthetic"])

check("still Qt-free at the end", "PySide6" not in sys.modules)

print(f"\n{sum(results)}/{len(results)} passed")
print("RESULT:", "PASS" if all(results) else "FAIL")
sys.exit(0 if all(results) else 1)
