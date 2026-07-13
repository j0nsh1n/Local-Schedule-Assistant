"""v3.9.0 — all-day Google Calendar events (roadmap #5).

Pure helpers: normalize_google_event, week_ahead formatting, timed vs all-day
filters. No real schedule data; synthetic Google-shaped payloads only."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
from PySide6.QtWidgets import QApplication

TMP = Path(tempfile.mkdtemp())
app.DATA_FILE = TMP / "a.json"
app.SETTINGS_FILE = TMP / "s.json"
app.CREDS_FILE = TMP / "c.json"
app.TOKEN_FILE = TMP / "t.json"
app.CHAT_FILE = TMP / "chat.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)
app.apply_theme("nocturne")

# ── normalize_google_event ───────────────────────────────────────────────────
timed = app.normalize_google_event({
    "id": "t1", "summary": "Dentist",
    "start": {"dateTime": "2026-07-14T10:00:00-07:00"},
    "end":   {"dateTime": "2026-07-14T11:00:00-07:00"},
})
check("timed → one entry", len(timed) == 1)
check("timed not allDay", timed[0]["allDay"] is False)
check("timed minutes", timed[0]["startMin"] == 600 and timed[0]["endMin"] == 660)
check("timed date", timed[0]["date"] == "2026-07-14")

ad = app.normalize_google_event({
    "id": "a1", "summary": "Essay due",
    "start": {"date": "2026-07-14"},
    "end":   {"date": "2026-07-15"},
})
check("single all-day → one entry", len(ad) == 1)
check("all-day flag", ad[0]["allDay"] is True)
check("all-day title", ad[0]["title"] == "Essay due")
check("all-day date", ad[0]["date"] == "2026-07-14")
check("all-day id namespaced", ad[0]["id"] == "a1:2026-07-14")

multi = app.normalize_google_event({
    "id": "h1", "summary": "Spring break",
    "start": {"date": "2026-07-14"},
    "end":   {"date": "2026-07-17"},
})
check("multi-day expands 3 days", len(multi) == 3)
check("multi-day dates exclusive end",
      [e["date"] for e in multi] == ["2026-07-14", "2026-07-15", "2026-07-16"])
check("multi all flagged", all(e["allDay"] for e in multi))

# end missing / end == start → still one day
one = app.normalize_google_event({
    "id": "x", "summary": "Holiday",
    "start": {"date": "2026-07-14"},
    "end":   {"date": "2026-07-14"},
})
check("end<=start still one day", len(one) == 1 and one[0]["date"] == "2026-07-14")

check("empty start → []", app.normalize_google_event({"id": "z", "summary": "x"}) == [])

# ── filters ──────────────────────────────────────────────────────────────────
mix = timed + ad
check("timed_cal filters out all-day",
      app.timed_cal_events(mix) == timed)
check("allday_cal keeps only all-day",
      app.allday_cal_events(mix) == ad)
check("missing allDay treated as timed",
      app.is_all_day_event({"startMin": 600, "endMin": 660}) is False)

# ── format + week_ahead ──────────────────────────────────────────────────────
check("brief all-day", "all day" in app.format_cal_event_brief(ad[0]).lower())
check("brief timed has times", "10:00" in app.format_cal_event_brief(timed[0]))

START = date(2026, 7, 14)
cal = {
    START.isoformat(): ad + timed,
    (START + timedelta(days=1)).isoformat(): multi[1:2],  # Spring break day 2
}
wa = app.week_ahead_lines(cal, START)
check("week_ahead mentions Essay due all day", "Essay due (all day)" in wa)
check("week_ahead mentions Dentist timed", "Dentist" in wa and "10:00" in wa)
check("all-day listed before timed on same day",
      wa.index("Essay due") < wa.index("Dentist"))
check("multi-day appears on next day", "Spring break (all day)" in wa)

# all-day alone still produces a week_ahead line
only_ad = {START.isoformat(): ad}
check("all-day-only day not omitted", "Essay due" in app.week_ahead_lines(only_ad, START))

# ── free time must ignore all-day ────────────────────────────────────────────
slots = app._free_slots([], app.DAY_START, app.DAY_END)
check("empty occ full day free",
      len(slots) == 1 and slots[0] == (app.DAY_START, app.DAY_END))

# MainWindow._cal_intervals excludes all-day
mw = app.MainWindow()
mw._cal_by_date = {START.isoformat(): mix}
iv = mw._cal_intervals(START.isoformat())
check("_cal_intervals only timed", iv == [(600, 660)])
gaps = mw._free_gaps(START.isoformat())
check("free gaps not blocked by all-day",
      any(e - s >= 60 for s, e in gaps))  # at least an hour free somewhere

# list_blocks lines include all-day
lines_ad = [f"[calendar all-day] {ev['title']}" for ev in app.allday_cal_events(mix)]
check("list style all-day line", lines_ad == ["[calendar all-day] Essay due"])

# banner text construction (what Day view shows)
ads = app.allday_cal_events(mix)
banner = "All day  ·  " + " · ".join(e.get("title") or "(no title)" for e in ads)
check("banner text", banner == "All day  ·  Essay due")

# Day page has banner widget
check("MainWindow has allday banner", hasattr(mw, "_allday_banner"))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
