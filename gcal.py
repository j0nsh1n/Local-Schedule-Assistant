"""Daily Scheduler — Google Calendar auth + fetch threads.

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

from datetime import datetime, date, timedelta
from typing import Optional, List, Dict

from PySide6.QtCore import (
    QThread, Signal,
)

import core
import theme
from core import new_id, parse_calendar_ids


def normalize_google_event(ev: dict) -> List[Dict]:
    """Turn one Google Calendar API event into 0+ day-scoped dicts used in
    `_cal_by_date`. Timed events → one entry; all-day → one entry per day in the
    half-open [start.date, end.date) range Google uses. Pure (no network)."""
    title = ev.get("summary") or "(no title)"
    eid   = ev.get("id") or new_id()
    start = ev.get("start") or {}
    end   = ev.get("end") or {}
    color = theme.C_INFO.name()

    if start.get("dateTime"):
        s_raw = start["dateTime"]
        e_raw = end.get("dateTime") or s_raw
        try:
            s  = datetime.fromisoformat(s_raw.replace("Z", "+00:00")).astimezone()
            en = datetime.fromisoformat(e_raw.replace("Z", "+00:00")).astimezone()
        except Exception:
            return []
        if en <= s:
            return []
        # Split across local midnights so overnight meetings (23:00→01:00) and
        # multi-day timed events still occupy free-slot / conflict checks.
        out: List[Dict] = []
        day = s.date()
        end_day = en.date()
        multi = end_day > day
        while day <= end_day:
            if day == s.date():
                sm = max(s.hour * 60 + s.minute, core.DAY_START)
            else:
                sm = core.DAY_START
            if day == en.date():
                em = min(en.hour * 60 + en.minute, core.DAY_END)
            else:
                em = core.DAY_END
            if em > sm:
                out.append({
                    "id": f"{eid}:{day.isoformat()}" if multi else eid,
                    "title": title, "startMin": sm, "endMin": em,
                    "type": "calendar", "color": color,
                    "date": day.isoformat(), "allDay": False,
                })
            day += timedelta(days=1)
        return out

    # All-day: start.date / end.date (end exclusive). Multi-day holidays expand.
    d0s = start.get("date")
    if not d0s:
        return []
    try:
        d0 = date.fromisoformat(d0s)
        d1s = end.get("date") or d0s
        d1 = date.fromisoformat(d1s)
    except Exception:
        return []
    if d1 <= d0:
        d1 = d0 + timedelta(days=1)
    out = []
    d = d0
    while d < d1:
        ds = d.isoformat()
        out.append({
            "id": f"{eid}:{ds}", "title": title,
            "startMin": 0, "endMin": 0,   # not a timed span — filtered from free slots
            "type": "calendar", "color": color, "date": ds, "allDay": True,
        })
        d += timedelta(days=1)
    return out

# ── Google Calendar threads ────────────────────────────────────────────────
class GoogleAuthThread(QThread):
    done  = Signal(object)  # credentials
    error = Signal(str)

    def run(self):
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request

            SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
            creds = None
            if core.TOKEN_FILE.exists():
                try:
                    creds = Credentials.from_authorized_user_file(str(core.TOKEN_FILE), SCOPES)
                except Exception:
                    pass

            if creds and creds.valid:
                self.done.emit(creds); return

            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    core.TOKEN_FILE.write_text(creds.to_json())
                    self.done.emit(creds); return
                except Exception:
                    pass

            if not core.CREDS_FILE.exists():
                self.error.emit(
                    "credentials.json not found.\n\n"
                    "Download it from Google Cloud Console:\n"
                    "APIs & Services → Credentials → OAuth 2.0 Client ID\n"
                    "(choose Desktop application) → Download JSON\n"
                    "then load it from the setup screen."
                ); return

            flow = InstalledAppFlow.from_client_secrets_file(str(core.CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
            core.TOKEN_FILE.write_text(creds.to_json())
            self.done.emit(creds)
        except ImportError:
            self.error.emit(
                "Google libraries not installed.\n"
                "Run:  pip install google-auth-oauthlib google-api-python-client"
            )
        except Exception as ex:
            self.error.emit(str(ex))


class CalFetchThread(QThread):
    done  = Signal(dict)   # {iso_date: [events]}
    error = Signal(str)    # total failure — nothing fetched (key is retried)
    warn  = Signal(str)    # partial failure — some calendars synced, some didn't

    def __init__(self, creds, start: date, end: date, calendar_ids: Optional[List[str]] = None):
        super().__init__()
        self.creds  = creds
        self._start = start     # NB: not 'self.start' — that is QThread.start()
        self._end   = end       # exclusive
        self._cals  = parse_calendar_ids(
            ",".join(calendar_ids) if calendar_ids else "primary")

    def _collect(self, svc) -> tuple:
        """Fetch all calendars, isolating failures per calendar so one bad ID
        (a typo in the calendar_ids setting) can't blank every calendar's events.
        Returns (by_date, failed_ids)."""
        t0 = datetime.combine(self._start, datetime.min.time()).astimezone()
        t1 = datetime.combine(self._end,   datetime.min.time()).astimezone()
        by_date: Dict[str, List[Dict]] = {}
        failed: List[str] = []
        for cal_id in self._cals:
            try:
                page = None
                while True:
                    res = svc.events().list(
                        calendarId=cal_id,
                        timeMin=t0.isoformat(), timeMax=t1.isoformat(),
                        singleEvents=True, orderBy="startTime",
                        maxResults=2500, pageToken=page,
                    ).execute()
                    for ev in res.get("items", []):
                        for entry in normalize_google_event(ev):
                            # Namespace id by calendar so two cals can't collide
                            entry = dict(entry)
                            entry["id"] = f"{cal_id}:{entry['id']}"
                            entry["calendarId"] = cal_id
                            by_date.setdefault(entry["date"], []).append(entry)
                    page = res.get("nextPageToken")
                    if not page:
                        break
            except Exception:
                failed.append(cal_id)
        return by_date, failed

    def run(self):
        try:
            from googleapiclient.discovery import build
            svc = build("calendar", "v3", credentials=self.creds)
            by_date, failed = self._collect(svc)
            if failed and len(failed) == len(self._cals):
                self.error.emit(f"Calendar fetch failed ({', '.join(failed)}).")
                return
            self.done.emit(by_date)
            if failed:
                self.warn.emit(
                    f"Couldn't fetch calendar(s): {', '.join(failed)} — check "
                    "the calendar IDs in Settings. Other calendars synced.")
        except Exception as ex:
            self.error.emit(str(ex))
