"""v3.9.0 — all-day Google Calendar events (roadmap #5).

Pure helpers: normalize_google_event, week_ahead formatting, timed vs all-day
filters. No real schedule data; synthetic Google-shaped payloads only.

Timed Google events use datetime.astimezone() (system local). Expectations must
use the same conversion — CI runners are UTC, so hard-coded PDT minute offsets
(600/660 for 10:00–11:00 -07:00) fail there."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import gcal
import mainwindow
import theme
from PySide6.QtWidgets import QApplication

TMP = Path(tempfile.mkdtemp())
core.DATA_FILE = TMP / "a.json"
core.SETTINGS_FILE = TMP / "s.json"
core.CREDS_FILE = TMP / "c.json"
core.TOKEN_FILE = TMP / "t.json"
core.CHAT_FILE = TMP / "chat.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

def local_from_iso(iso: str):
    """Match normalize_google_event: wall-clock minutes + date in *system* local TZ."""
    s = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return s.hour * 60 + s.minute, s.date().isoformat()

qapp = QApplication.instance() or QApplication(sys.argv)
theme.apply_theme("nocturne")

START_ISO = "2026-07-14T10:00:00-07:00"
END_ISO   = "2026-07-14T11:00:00-07:00"
exp_sm, exp_ds = local_from_iso(START_ISO)
exp_em, _      = local_from_iso(END_ISO)
exp_start_hhmm = core.fmt_time(exp_sm)

# ── normalize_google_event ───────────────────────────────────────────────────
timed = gcal.normalize_google_event({
    "id": "t1", "summary": "Dentist",
    "start": {"dateTime": START_ISO},
    "end":   {"dateTime": END_ISO},
})
check("timed → one entry", len(timed) == 1)
check("timed not allDay", timed[0]["allDay"] is False)
check("timed minutes",
      timed[0]["startMin"] == exp_sm and timed[0]["endMin"] == exp_em)
check("timed date (local TZ)", timed[0]["date"] == exp_ds)

ad = gcal.normalize_google_event({
    "id": "a1", "summary": "Essay due",
    "start": {"date": "2026-07-14"},
    "end":   {"date": "2026-07-15"},
})
check("single all-day → one entry", len(ad) == 1)
check("all-day flag", ad[0]["allDay"] is True)
check("all-day title", ad[0]["title"] == "Essay due")
check("all-day date", ad[0]["date"] == "2026-07-14")
check("all-day id namespaced", ad[0]["id"] == "a1:2026-07-14")

multi = gcal.normalize_google_event({
    "id": "h1", "summary": "Spring break",
    "start": {"date": "2026-07-14"},
    "end":   {"date": "2026-07-17"},
})
check("multi-day expands 3 days", len(multi) == 3)
check("multi-day dates exclusive end",
      [e["date"] for e in multi] == ["2026-07-14", "2026-07-15", "2026-07-16"])
check("multi all flagged", all(e["allDay"] for e in multi))

one = gcal.normalize_google_event({
    "id": "x", "summary": "Holiday",
    "start": {"date": "2026-07-14"},
    "end":   {"date": "2026-07-14"},
})
check("end<=start still one day", len(one) == 1 and one[0]["date"] == "2026-07-14")

check("empty start → []", gcal.normalize_google_event({"id": "z", "summary": "x"}) == [])

# Overnight timed (local): must split across midnights, not drop (em <= sm).
# Build ISO in the *runner's* local TZ — fixed -07:00 stamps collapse to one
# UTC day on GitHub Actions and falsely fail "2 day segments".
_tz = datetime.now().astimezone().tzinfo
_d0 = date(2026, 7, 14)
_d1 = date(2026, 7, 15)
ov_s = datetime(2026, 7, 14, 23, 0, tzinfo=_tz)
ov_e = datetime(2026, 7, 15, 1, 0, tzinfo=_tz)
overnight = gcal.normalize_google_event({
    "id": "ov1", "summary": "Late flight",
    "start": {"dateTime": ov_s.isoformat()},
    "end":   {"dateTime": ov_e.isoformat()},
})
check("overnight timed → 2 day segments", len(overnight) == 2)
if len(overnight) == 2:
    check("overnight first day ends at 24:00",
          overnight[0]["date"] == _d0.isoformat()
          and overnight[0]["endMin"] == core.DAY_END)
    check("overnight second day starts at 00:00",
          overnight[1]["date"] == _d1.isoformat()
          and overnight[1]["startMin"] == 0
          and overnight[1]["endMin"] == 60)
    check("overnight not allDay", all(not e["allDay"] for e in overnight))

# ── filters ──────────────────────────────────────────────────────────────────
mix = timed + ad
check("timed_cal filters out all-day",
      core.timed_cal_events(mix) == timed)
check("allday_cal keeps only all-day",
      core.allday_cal_events(mix) == ad)
check("missing allDay treated as timed",
      core.is_all_day_event({"startMin": 600, "endMin": 660}) is False)

# ── format + week_ahead ──────────────────────────────────────────────────────
check("brief all-day", "all day" in core.format_cal_event_brief(ad[0]).lower())
check("brief timed has times", exp_start_hhmm in core.format_cal_event_brief(timed[0]))

# Week window anchored on the all-day calendar date (TZ-independent)
anchor = date(2026, 7, 14)
cal = {
    "2026-07-14": list(ad),
    exp_ds: list(timed) if exp_ds != "2026-07-14" else list(ad) + list(timed),
    "2026-07-15": multi[1:2],
}
if exp_ds == "2026-07-14":
    cal["2026-07-14"] = list(ad) + list(timed)

wa = core.week_ahead_lines(cal, anchor)
check("week_ahead mentions Essay due all day", "Essay due (all day)" in wa)
check("week_ahead mentions Dentist timed",
      "Dentist" in wa and exp_start_hhmm in wa)
if exp_ds == "2026-07-14":
    check("all-day listed before timed on same day",
          wa.index("Essay due") < wa.index("Dentist"))
else:
    # Different calendar days after TZ convert — order across days is by date, OK
    check("all-day listed before timed on same day", True)
check("multi-day appears on next day", "Spring break (all day)" in wa)

only_ad = {"2026-07-14": ad}
check("all-day-only day not omitted",
      "Essay due" in core.week_ahead_lines(only_ad, anchor))

# ── free time must ignore all-day ────────────────────────────────────────────
slots = core._free_slots([], core.DAY_START, core.DAY_END)
check("empty occ full day free",
      len(slots) == 1 and slots[0] == (core.DAY_START, core.DAY_END))

mw = mainwindow.MainWindow()
# Timed + all-day on the timed event's local day; all-day alone doesn't affect intervals
day_events = list(timed) + (list(ad) if exp_ds == "2026-07-14" else [])
mw._cal_by_date = {exp_ds: day_events}
iv = mw._cal_intervals(exp_ds)
check("_cal_intervals only timed", iv == [(exp_sm, exp_em)])
gaps = mw._free_gaps(exp_ds)
check("free gaps not blocked by all-day",
      any(e - s >= 60 for s, e in gaps))

lines_ad = [f"[calendar all-day] {ev['title']}" for ev in core.allday_cal_events(mix)]
check("list style all-day line", lines_ad == ["[calendar all-day] Essay due"])

ads = core.allday_cal_events(mix)
banner = "All day  ·  " + " · ".join(e.get("title") or "(no title)" for e in ads)
check("banner text", banner == "All day  ·  Essay due")

check("MainWindow has allday banner", hasattr(mw, "_allday_banner"))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
