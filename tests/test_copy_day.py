"""v3.6.0 — copy_day calendar-awareness + weekday date resolution (bug report 2026-07-06).
Offscreen, synthetic data only (DATA_FILE/etc. redirected to a temp dir — never the real
store). Covers: the default (non-merge) copy_day now pushes copies off the target day's
read-only meetings (the reported "copied to Wednesday but sat on the meeting" bug); it stays
a no-op on a clean day; merge still keeps existing blocks; overflow past end-of-day is
reported as dropped; resolve_date() maps weekday names to the correct date; and the prompt
tells the model to pass the weekday WORD rather than compute a date (the "Thursday -> Jul 10"
bug was the model doing its own date math)."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aipanel
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

MON = date(2026, 7, 6)     # viewed day is a Monday
WED = "2026-07-08"
THU = "2026-07-09"

def blk(ds, sm, em, title):
    return {"id": f"{title}{sm}", "date": ds, "startMin": sm, "endMin": em,
            "type": "study", "color": "#888", "title": title}
def mtg(ds, sm, em, title="Meeting"):
    return {"id": f"m{sm}", "date": ds, "startMin": sm, "endMin": em,
            "type": "calendar", "color": "#57f", "title": title}
def overlaps(a, s, e):
    return a["startMin"] < e and a["endMin"] > s

def fresh(src_blocks, cal=None):
    mw = mainwindow.MainWindow()
    mw._cur_date = MON
    mw._all_acts = list(src_blocks)
    mw._cal_by_date = cal or {}
    return mw

SRC = [blk(MON.isoformat(), 540, 600, "Math"), blk(MON.isoformat(), 720, 780, "AP Work")]

# ── non-merge onto a day WITH a meeting → copies pushed off it (the fix) ───────
mw = fresh(SRC, {WED: [mtg(WED, 720, 780)]})
res = mw._ai_execute("copy_day", {"to_date": "wednesday"})
dst = [a for a in mw._all_acts if a["date"] == WED]
check("copied both blocks to Wednesday", len(dst) == 2)
check("NO copied block overlaps the meeting", not any(overlaps(a, 720, 780) for a in dst))
check("the conflicting block was shifted after the meeting",
      any(a["title"] == "AP Work" and a["startMin"] >= 780 for a in dst))
check("the non-conflicting block kept its time",
      any(a["title"] == "Math" and a["startMin"] == 540 for a in dst))
check("result mentions the shift", "shifted to clear a meeting" in res)

# ── non-merge onto a CLEAN day → exact copy, no shifting (behavior preserved) ──
mw = fresh(SRC)
res = mw._ai_execute("copy_day", {"to_date": "Thursday"})
dst = [a for a in mw._all_acts if a["date"] == THU]
check("weekday name 'Thursday' resolved to the correct date (Jul 9)", len(dst) == 2)
check("clean-day copy keeps exact times (no-op sequentialize)",
      sorted((a["startMin"], a["endMin"]) for a in dst) == [(540, 600), (720, 780)])
check("clean-day copy adds no shift note", "shifted" not in res)

# ── merge onto a day with an existing block + a meeting ───────────────────────
mw = fresh(SRC, {WED: [mtg(WED, 720, 780)]})
mw._all_acts += [blk(WED, 480, 540, "Existing")]
mw._ai_execute("copy_day", {"to_date": "wednesday", "merge": True})
dst = [a for a in mw._all_acts if a["date"] == WED]
check("merge keeps the target's existing block", any(a["title"] == "Existing" for a in dst))
check("merge added the copies too", any(a["title"] == "AP Work" for a in dst))
check("merge: nothing overlaps the meeting", not any(overlaps(a, 720, 780) for a in dst))

# ── overflow past end-of-day is reported as dropped ───────────────────────────
mw = fresh([blk(MON.isoformat(), 1380, 1410, "Late")], {WED: [mtg(WED, 1380, 1440)]})
res = mw._ai_execute("copy_day", {"to_date": "wednesday"})
dst = [a for a in mw._all_acts if a["date"] == WED]
check("a block that can't fit after the meeting is dropped", len(dst) == 0)
check("result reports the drop", "dropped" in res)

# ── resolve_date maps weekday names correctly (why the model shouldn't compute) ─
check("resolve_date('Thursday', Mon Jul 6) == Jul 9", core.resolve_date("Thursday", MON) == THU)
check("resolve_date('wednesday') == Jul 8", core.resolve_date("wednesday", MON) == WED)
check("resolve_date('monday') jumps a full week (never returns base)",
      core.resolve_date("monday", MON) == "2026-07-13")

# ── the prompt steers the model to pass the weekday word, not a computed date ──
prompt = aipanel.AIPanel(lambda: {"cal_events": [], "activities": [], "week_ahead": "",
                              "view_date": MON.isoformat(), "today": MON.isoformat(),
                              "weekday": "Monday", "now_min": 600, "viewing_today": True})._sys_prompt()
check("prompt tells the model NOT to compute the date itself",
      "date arithmetic is unreliable" in prompt or "NOT a date you computed" in prompt)
check("prompt shows the copy-to-weekday example", 'copy_day(to_date="Thursday")' in prompt)

n = len(results)
print(f"RESULT: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{n})")
sys.exit(0 if all(results) else 1)
