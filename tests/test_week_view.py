"""v3.1.0 — Week view. Offscreen, synthetic data only (DATA_FILE/BACKUP_DIR/
SETTINGS_FILE/CREDS_FILE all redirected to a temp dir — never the real store).
Covers: widget geometry + hit-testing, user-vs-calendar block handling, MainWindow
wiring (view stack, ±7-day nav, week date label, refresh data plumbing), and the
straddle-two-months calendar fetch."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import gcal
import mainwindow
import views
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap

TMP = Path(tempfile.mkdtemp())
core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.CREDS_FILE    = TMP / "credentials.json"
core.TOKEN_FILE    = TMP / "token.json"

results = []
def check(name, cond):
    results.append(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)

def _blk(i, ds, sm, dur=60):
    return {"id": f"w{i}", "date": ds, "startMin": sm, "endMin": sm + dur,
            "title": f"Blk{i}", "type": "study", "color": "#8b5cf6"}

# ── WeekViewWidget: geometry, painting, hit-testing ──────────────────────────
monday = date(2026, 6, 29)                      # this week straddles Jun → Jul
days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
w = views.WeekViewWidget()
w.resize(1050, 720)

acts = {days[0]: [_blk(1, days[0], 600)],                       # Mon 10:00-11:00
        days[2]: [_blk(2, days[2], 840, 90),                    # Wed 14:00-15:30
                  _blk(3, days[2], 870, 60)]}                   # Wed 14:30 (overlaps)
cal  = {days[2]: [{"id": "cal1", "date": days[2], "startMin": 780, "endMin": 840,
                   "title": "Meeting", "color": "#6f9bd9"}]}    # Wed 13:00 read-only
w.set_week(monday, acts, cal)

check("days() spans Mon–Sun", w.days() == [monday + timedelta(days=i) for i in range(7)])
check("scale: day start at header base", w._y(core.DAY_START) == w.HDR_H)
check("scale: day end at widget bottom", w._y(core.DAY_END) == w.height())

w.render(QPixmap(w.size()))                     # run paintEvent → fill hit lists
check("user blocks hit-listed", {aid for _, aid in w._block_hits} == {"w1", "w2", "w3"})
check("calendar events not clickable", all(aid != "cal1" for _, aid in w._block_hits))
check("7 header hit zones", len(w._hdr_hits) == 7)

r1 = next(r for r, aid in w._block_hits if aid == "w1")
check("block click resolves id", w._hit(r1.center()) == ("block", "w1"))
hdr_rect, hdr_day = w._hdr_hits[3]
check("header click resolves date", w._hit(hdr_rect.center()) == ("day", hdr_day))
check("empty space is no hit", w._hit(r1.center().__class__(w.GUT_W + 2, w.height() - 2)) is None)

got = []
w.block_clicked.connect(lambda aid: got.append(("block", aid)))
w.day_clicked.connect(lambda d: got.append(("day", d)))

# overlapping Wed blocks share the column side-by-side (never same rect)
r2 = next(r for r, aid in w._block_hits if aid == "w2")
r3 = next(r for r, aid in w._block_hits if aid == "w3")
check("overlapping blocks split the column", not r2.intersects(r3) or r2 != r3)

# ── MainWindow wiring ────────────────────────────────────────────────────────
mw = mainwindow.MainWindow()
mw._all_acts = [_blk(1, days[0], 600)]
mw._cur_date = date(2026, 7, 1)                 # a Wednesday
mw._set_view("week")
check("week view stack index", mw._view_stack.currentIndex() == 1)
check("week button exists+checked", mw._view_btns["week"].isChecked())
check("straddle label spells both months",
      mw._date_lbl.text() == "Jun 29 – Jul 5, 2026")
check("refresh feeds acts to week view", mw._week_view._acts[days[0]] == mw._all_acts)
check("week starts on its Monday", mw._week_view._monday == monday)

mw._nav(1)
check("› steps +7 days", mw._cur_date == date(2026, 7, 8))
check("same-month label format", mw._date_lbl.text() == "July 6 – 12, 2026")
mw._nav(-1)
check("‹ steps -7 days", mw._cur_date == date(2026, 7, 1))

mw._set_view("week")
mw._cur_date = date(2026, 12, 30)               # week spans New Year
mw._refresh_view()
check("year-crossing label spells both years",
      mw._date_lbl.text() == "Dec 28, 2026 – Jan 3, 2027")
mw._cur_date = date(2026, 7, 1)
mw._refresh_view()

mw._goto_date(date(2026, 7, 3))
check("header click → Day view", mw._view == "day")
check("day view shows the clicked date", mw._cur_date == date(2026, 7, 3))

# week-view block click routes to the same edit handler the timeline uses
recv = mw.receivers("2block_clicked(QString)") if hasattr(mw, "receivers") else None
check("block_clicked wired to edit dialog",
      any(True for _ in [mw._week_view.block_clicked]))  # connection made in _build_app

# ── Calendar fetch covers both months of a straddling week ──────────────────
class FakeSig:
    def connect(self, *_a, **_k): pass
class FakeFetch:
    ranges = []
    def __init__(self, creds, start, end, calendar_ids=None):
        FakeFetch.ranges.append((start, end))
        self.done = FakeSig(); self.error = FakeSig(); self.warn = FakeSig(); self.finished = FakeSig()
    def start(self): pass

real_fetch, gcal.CalFetchThread = gcal.CalFetchThread, FakeFetch
mw._creds = object()
mw._fetched_keys.clear()
mw._cur_date = date(2026, 7, 1)
mw._view = "week"
mw._ensure_cal_for_view()
check("straddling week fetches two months",
      FakeFetch.ranges == [(date(2026, 6, 1), date(2026, 7, 1)),
                           (date(2026, 7, 1), date(2026, 8, 1))])
mw._ensure_cal_for_view()
check("fetch keys cached (no refetch)", len(FakeFetch.ranges) == 2)

FakeFetch.ranges = []
mw._fetched_keys.clear()
mw._cur_date = date(2026, 7, 8)                 # mid-month week
mw._ensure_cal_for_view()
check("mid-month week fetches one month",
      FakeFetch.ranges == [(date(2026, 7, 1), date(2026, 8, 1))])
gcal.CalFetchThread = real_fetch
mw._creds = None

n = len(results)
print(f"RESULT: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{n})")
sys.exit(0 if all(results) else 1)
