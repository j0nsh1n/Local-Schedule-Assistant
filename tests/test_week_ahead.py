"""v3.4.0 — multi-day AI calendar context (roadmap #4). Offscreen, synthetic data only
(DATA_FILE/SETTINGS_FILE/CREDS_FILE redirected to a temp dir — never the real store).
Covers the pure week_ahead_lines(), the _month_range() key math (incl. Dec→Jan rollover),
and the MainWindow/AIPanel wiring (ctx field, the 'THE WEEK AHEAD' prompt block, and the
this+next-month prefetch keys)."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal

TMP = Path(tempfile.mkdtemp())
app.DATA_FILE     = TMP / "activities.json"
app.BACKUP_DIR    = TMP / "backups"
app.SETTINGS_FILE = TMP / "settings.json"
app.CREDS_FILE    = TMP / "credentials.json"
app.TOKEN_FILE    = TMP / "token.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)

def ev(d, sm, em, title):
    ds = d.isoformat()
    return {"id": f"e{ds}-{sm}", "date": ds, "startMin": sm, "endMin": em,
            "title": title, "type": "calendar", "color": "#57f"}

START = date(2026, 7, 6)   # fixed anchor — week_ahead_lines is pure (no clock read)

# ── Pure week_ahead_lines ─────────────────────────────────────────────────────
check("empty cache → empty string", app.week_ahead_lines({}, START) == "")

cal = {
    START.isoformat():                       [ev(START, 600, 660, "Dentist")],
    (START + timedelta(days=1)).isoformat(): [ev(START + timedelta(days=1), 780, 840, "Lunch w/ Sam"),
                                              ev(START + timedelta(days=1), 540, 570, "Standup")],
    (START + timedelta(days=3)).isoformat(): [ev(START + timedelta(days=3), 900, 960, "Robotics")],
    (START + timedelta(days=9)).isoformat(): [ev(START + timedelta(days=9), 600, 660, "Too far")],
    (START - timedelta(days=1)).isoformat(): [ev(START - timedelta(days=1), 600, 660, "Yesterday")],
}
out   = app.week_ahead_lines(cal, START)
lines = out.splitlines()

check("only in-window days with events appear", len(lines) == 3)
check("offset 0 labeled today", "(today)" in lines[0] and "Dentist" in lines[0])
check("offset 1 labeled tomorrow", "(tomorrow)" in lines[1])
lbl = (START + timedelta(days=3)).strftime("%a %b %d")
check(f"offset >=2 labeled weekday+date ({lbl})", f"({lbl})" in lines[2])
check("day beyond 7-day window excluded", "Too far" not in out)
check("day before start excluded", "Yesterday" not in out)
check("events sorted by start time", lines[1].index("Standup") < lines[1].index("Lunch w/ Sam"))
check("multiple events joined with '; '", "; " in lines[1])
check("times formatted 24h", "09:00–09:30" in lines[1])
check("each line carries the ISO date", lines[0].startswith(f"  {START.isoformat()} "))

# window is inclusive of its last day and its size is configurable
near = {(START + timedelta(days=6)).isoformat(): [ev(START + timedelta(days=6), 600, 660, "Day7")]}
check("last day of 7-day window included (offset 6)", "Day7" in app.week_ahead_lines(near, START))
check("shrinking the window excludes it", app.week_ahead_lines(near, START, days=6) == "")

# ── _month_range key math ─────────────────────────────────────────────────────
k, s, e = app.MainWindow._month_range(2026, 7)
check("month key format", k == "m2026-7")
check("month start is the 1st", s == date(2026, 7, 1))
check("month end is exclusive next-month 1st", e == date(2026, 8, 1))
k, s, e = app.MainWindow._month_range(2026, 12)
check("December rolls end to next January", k == "m2026-12" and e == date(2027, 1, 1))

# ── MainWindow / AIPanel wiring ───────────────────────────────────────────────
mw = app.MainWindow()
today = date.today()
mw._cal_by_date = {
    today.isoformat():                       [ev(today, 600, 660, "Dentist")],
    (today + timedelta(days=2)).isoformat(): [ev(today + timedelta(days=2), 900, 990, "Robotics Club")],
}
ctx = mw._ai_ctx()
check("_ai_ctx exposes week_ahead", "week_ahead" in ctx)
check("week_ahead anchored on the real today",
      "Dentist" in ctx["week_ahead"] and "Robotics Club" in ctx["week_ahead"])

prompt = mw._ai_panel._sys_prompt()
check("prompt has THE WEEK AHEAD section", "THE WEEK AHEAD" in prompt)
check("prompt lists an upcoming event", "Robotics Club" in prompt)

# prefetch targets exactly this + next month (stub _fetch_ranges — no thread/network)
mw._creds = object()          # gate passes
captured = []
mw._fetch_ranges = lambda ranges: captured.extend(ranges)
mw._prefetch_ai_months()
nxt  = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
keys = {r[0] for r in captured}
check("prefetch requests this + next month",
      keys == {f"m{today.year}-{today.month}", f"m{nxt.year}-{nxt.month}"})

# with no events loaded the section is omitted entirely (keep the prompt lean)
mw._cal_by_date = {}
check("no week-ahead section when empty", "THE WEEK AHEAD" not in mw._ai_panel._sys_prompt())

# ── End-to-end orchestration (fake fetch thread, no Google network) ────────────
# Drives the REAL _refresh_cal → _ensure_cal_for_view + _prefetch_ai_months →
# _fetch_ranges → _on_cal path (nothing stubbed) to prove the two entry points
# dedup (current month fetched ONCE) and the fetched data reaches the AI prompt.
FETCHES = []
SYNTH = {
    (today.year, today.month): {
        today.isoformat():                       [ev(today, 600, 660, "Dentist")],
        (today + timedelta(days=2)).isoformat(): [ev(today + timedelta(days=2), 900, 990, "Robotics Club")],
    },
    (nxt.year, nxt.month): {nxt.isoformat(): [ev(nxt, 540, 600, "Next-month meeting")]},
}
class FakeCalThread(QObject):
    done = Signal(dict); error = Signal(str); warn = Signal(str); finished = Signal()
    def __init__(self, creds, start, end, calendar_ids=None):
        super().__init__(); self._s = start; FETCHES.append(start)
    def start(self):                       # synchronous: emit synthetic events, then finish
        self.done.emit(SYNTH.get((self._s.year, self._s.month), {})); self.finished.emit()
app.CalFetchThread = FakeCalThread

mw2 = app.MainWindow()
mw2._creds = object()          # gate passes; no real auth
mw2._refresh_cal()             # the integrated on-connect entry point

check("current month fetched exactly once (no double-fetch across entry points)",
      FETCHES.count(date(today.year, today.month, 1)) == 1)
check("both this + next month fetched", len(FETCHES) == 2 and date(nxt.year, nxt.month, 1) in FETCHES)
check("fetched data landed in the cache via _on_cal",
      any(e["title"] == "Dentist" for e in mw2._cal_by_date.get(today.isoformat(), [])))
check("fetch threads cleaned up (finished handler ran)", mw2._cal_threads == [])
check("AI prompt reflects the fetched week after a real fetch",
      "THE WEEK AHEAD" in mw2._ai_panel._sys_prompt()
      and "Robotics Club" in mw2._ai_panel._sys_prompt())

n = len(results)
print(f"RESULT: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{n})")
sys.exit(0 if all(results) else 1)
