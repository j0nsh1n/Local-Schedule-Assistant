"""Daily Scheduler — constants, pure helpers, storage, settings (no Qt).

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

import sys
import json
import uuid
import shutil
import os
import re
import traceback
import faulthandler
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict




# ── App metadata ───────────────────────────────────────────────────────────
__version__  = "4.2.0"
APP_VERSION  = __version__

# Auto-update check (roadmap #2): compare the newest GitHub release's tag against
# APP_VERSION once per launch + daily. Returns 404 while the repo is PRIVATE — the
# check fails silently and simply lights up the day the repo goes public.
GITHUB_REPO        = "j0nsh1n/Local-Schedule-Assistant"
RELEASES_PAGE      = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# ── App data paths ─────────────────────────────────────────────────────────
DATA_DIR   = Path.home() / ".daily-scheduler"
DATA_FILE  = DATA_DIR / "activities.json"
CREDS_FILE = DATA_DIR / "credentials.json"
TOKEN_FILE = DATA_DIR / "token.json"
CRASH_LOG  = DATA_DIR / "crash.log"   # native fatal faults (faulthandler)
ERROR_LOG  = DATA_DIR / "app.log"     # unhandled Python tracebacks (sys.excepthook)
CHAT_FILE  = DATA_DIR / "chat.json"   # v3.8.0: AI panel transcript (crash-proof)
CHAT_SAVE_MIN_SEC = 0.4               # throttle mid-stream writes
DATA_DIR.mkdir(exist_ok=True)

# ── Layout constants ───────────────────────────────────────────────────────
DAY_START_H = 0
DAY_END_H   = 24
DAY_START   = DAY_START_H * 60   # minutes from midnight (full 24h day)
DAY_END     = DAY_END_H   * 60
HOUR_PX     = 96                  # pixels per hour on timeline (scrolls; centers on now)
GUTTER_W    = 64                  # width of time-label column
DEFAULT_MODEL = "qwen2.5:14b"     # better at tool-use/reasoning than llama3.1:8b
DEFAULT_THEME = "nocturne"

# ── Activity types ─────────────────────────────────────────────────────────
# Expanded set for high-school life. Tool schemas + AI prompt are generated from
# this list, so adding a type here is enough for pickers and the model.
ACTIVITY_TYPES = [
    {"id": "assignments", "label": "Assignments",      "icon": "📝", "color": "#ef4444"},
    {"id": "project",     "label": "Projects",         "icon": "🛠",  "color": "#f59e0b"},
    {"id": "study",       "label": "Study",            "icon": "📚", "color": "#8b5cf6"},
    {"id": "class",       "label": "Class / School",   "icon": "🏫", "color": "#3b82f6"},
    {"id": "reading",     "label": "Reading",          "icon": "📖", "color": "#a78bfa"},
    {"id": "extra",       "label": "Extracurriculars", "icon": "🎯", "color": "#ec4899"},
    {"id": "club",        "label": "Clubs",            "icon": "🏛", "color": "#d946ef"},
    {"id": "music",       "label": "Music / Practice", "icon": "🎵", "color": "#14b8a6"},
    {"id": "creative",    "label": "Creative / Art",   "icon": "🎨", "color": "#f472b6"},
    {"id": "gaming",      "label": "Anime/Gaming",     "icon": "🎮", "color": "#06b6d4"},
    {"id": "social",      "label": "Social",           "icon": "👥", "color": "#22d3ee"},
    {"id": "exercise",    "label": "Exercise",         "icon": "💪", "color": "#10b981"},
    {"id": "meals",       "label": "Meals",            "icon": "🍽", "color": "#f97316"},
    {"id": "chores",      "label": "Chores",           "icon": "🏠", "color": "#a3a3a3"},
    {"id": "work",        "label": "Work / Job",       "icon": "💼", "color": "#64748b"},
    {"id": "commute",     "label": "Commute",          "icon": "🚌", "color": "#78716c"},
    {"id": "health",      "label": "Health",           "icon": "🏥", "color": "#fb7185"},
    {"id": "free",        "label": "Free / Rest",      "icon": "☕", "color": "#94a3b8"},
    {"id": "sleep",       "label": "Sleep",            "icon": "🌙", "color": "#6366f1"},
]

# Map legacy type ids (from older data) onto the current set, so existing blocks
# keep a sensible category/color after this change.
_OLD_TYPE_MAP = {"anime": "gaming", "friends": "extra", "social": "social",
                 "gym": "exercise", "workout": "exercise", "rest": "free",
                 "break": "free", "school": "class", "lesson": "class"}

def activity_type_prompt_block() -> str:
    """Human lines for the AI system prompt — always stays in sync with ACTIVITY_TYPES."""
    lines = [
        "ACTIVITY TYPES — set each block's \"type\" to what the user will actually be "
        "DOING (judge by the activity itself, not the blocks around it):"
    ]
    for t in ACTIVITY_TYPES:
        lines.append(f"  {t['id']:<12} – {t['label']}")
    lines += [
        "TYPE RULES (the model often gets these wrong — follow them):",
        "  - A BREAK or REST between work → use \"free\" (or \"gaming\" for entertainment,",
        "    \"exercise\" for a physical break, \"meals\" for a snack). NEVER label a break",
        "    as \"study\", \"assignments\", \"project\", or \"class\".",
        "  - A break between two study blocks is still a break — don't copy the surrounding type.",
        "  - split_block focus chunks keep the task type; breaks default to \"free\"",
        "    (override with break_type).",
        "  - School lessons / periods → \"class\". Homework due soon → \"assignments\".",
        "  - Hangouts → \"social\". Band/orchestra practice → \"music\".",
    ]
    return "\n".join(lines)

# ── Pure helper functions ──────────────────────────────────────────────────
def min_to_y(minutes: int) -> int:
    return int((minutes - DAY_START) / 60 * HOUR_PX)

def y_to_min(y: int) -> int:
    return int(DAY_START + y / HOUR_PX * 60)

def fmt_time(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)   # 24-hour HH:MM (e.g. 09:00, 14:30, 24:00)
    return f"{h:02d}:{m:02d}"

def fmt_dur(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"

def strip_v(tag: str) -> str:
    """'v3.2.0' → '3.2.0'; leaves an already-bare version untouched."""
    t = (tag or "").strip()
    return t[1:] if t[:1] in ("v", "V") else t

def _version_tuple(s: str) -> tuple:
    """('v3.1.0' | '3.1.0' | '3.1.0-beta.2') → (3, 1, 0). Strips a leading 'v' and
    any '-'/'+' pre-release or build suffix, then parses the dotted integers.
    Returns () when nothing parseable remains (so garbage never ranks as an update)."""
    core = strip_v(s).split("-")[0].split("+")[0].strip()
    if not core:
        return ()
    try:
        return tuple(int(p) for p in core.split("."))
    except ValueError:
        return ()

def is_newer_version(latest: str, current: str) -> bool:
    """True iff release tag `latest` is a strictly newer version than `current`.
    Fails CLOSED: an unparseable/empty `latest` returns False so we never nag on
    a malformed tag. Shorter versions are zero-padded ('3.2' == '3.2.0')."""
    lt = _version_tuple(latest)
    if not lt:
        return False
    ct = _version_tuple(current)
    n = max(len(lt), len(ct))
    lt += (0,) * (n - len(lt))
    ct += (0,) * (n - len(ct))
    return lt > ct

def now_next_summary(blocks: List[Dict], now_min: int) -> str:
    """One-line 'Now / Next' status for minute-of-day `now_min`. `blocks` = today's
    items (each with startMin/endMin/title). Returns '' when nothing is current OR
    upcoming (e.g. after the last block). Pure so it's unit-testable without a clock."""
    def short(b) -> str:
        t = (b.get("title") or "").strip() or "Untitled"
        return t if len(t) <= 30 else t[:29] + "…"
    ordered = sorted(blocks, key=lambda b: b["startMin"])
    cur = next((b for b in ordered if b["startMin"] <= now_min < b["endMin"]), None)
    # "Next" is the block starting after the current one ENDS — skip blocks that merely
    # overlap the current one (user blocks and calendar events mix here, so overlaps are
    # realistic). With no current block, it's the next block starting after now.
    after = cur["endMin"] if cur else now_min
    nxt = next((b for b in ordered if b["startMin"] > now_min and b["startMin"] >= after), None)
    parts = []
    if cur:
        parts.append(f"Now: {short(cur)} · {fmt_dur(cur['endMin'] - now_min)} left")
    if nxt:
        when = fmt_time(nxt["startMin"])
        if cur:
            parts.append(f"Next: {short(nxt)} at {when}")
        else:
            parts.append(f"Next: {short(nxt)} at {when} (in {fmt_dur(nxt['startMin'] - now_min)})")
    return "  →  ".join(parts)

def is_all_day_event(e: Dict) -> bool:
    """True for Google all-day (and multi-day) events — they do not occupy a time span."""
    return bool(e.get("allDay"))

def timed_cal_events(events: List[Dict]) -> List[Dict]:
    return [e for e in events if not is_all_day_event(e)]

def allday_cal_events(events: List[Dict]) -> List[Dict]:
    return [e for e in events if is_all_day_event(e)]

def format_cal_event_brief(e: Dict) -> str:
    """One short phrase for AI / banners: timed with HH:MM range, all-day labeled."""
    title = e.get("title") or "(no title)"
    if is_all_day_event(e):
        return f"{title} (all day)"
    return f"{title} {fmt_time(e['startMin'])}–{fmt_time(e['endMin'])}"

def week_ahead_lines(cal_by_date: Dict[str, List[Dict]], start: date, days: int = 7) -> str:
    """Compact multi-day calendar preview for the AI system prompt: read-only Google
    events over [start, start+days), one line per day, days with no events omitted.
    `start` is the anchor (today) so offset 0/1 label as today/tomorrow — pure (no clock
    read), so it's unit-testable. Returns '' when nothing is scheduled in the window.
    All-day events render as 'Title (all day)'; timed keep HH:MM–HH:MM."""
    out = []
    for i in range(days):
        d  = start + timedelta(days=i)
        raw = cal_by_date.get(d.isoformat(), [])
        if not raw:
            continue
        # All-day first (deadlines/holidays), then timed by start.
        ad  = allday_cal_events(raw)
        tm  = sorted(timed_cal_events(raw), key=lambda e: e["startMin"])
        ev  = ad + tm
        label = {0: "today", 1: "tomorrow"}.get(i, d.strftime("%a %b %d"))
        items = "; ".join(format_cal_event_brief(e) for e in ev)
        out.append(f"  {d.isoformat()} ({label}): {items}")
    return "\n".join(out)

def today_str() -> str:
    return date.today().isoformat()

def new_id() -> str:
    return str(uuid.uuid4())[:8]

# ── Crash / error logging ───────────────────────────────────────────────────
# A "flight recorder" for a --windowed app that has no console: persist WHY it died.
#   app.log   — unhandled Python tracebacks (sys.excepthook), rotated at ~1 MB → .old
#   crash.log — native fatal faults (segfault/abort, e.g. a GPU-driver crash) via
#               faulthandler, dumping every thread's stack.
# Privacy: tracebacks + faulthandler dumps record frames/lines only, NEVER local values,
# so no schedule data is written (proven by test_error_log.py). All best-effort — a
# logging failure must never block startup or silence the app.
_crash_fh = None   # crash.log kept open for the process lifetime (faulthandler writes to it)

def _rotate_log(path: Path, max_bytes: int = 1_000_000) -> None:
    """Single-generation rotation: once `path` passes max_bytes, move it to `<path>.old`
    (replacing any previous .old) so the log can't grow without bound."""
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            path.replace(path.with_name(path.name + ".old"))
    except Exception:
        pass

def log_exception(exc_type, exc, tb) -> None:
    """sys.excepthook: append an unhandled exception's traceback to app.log (rotated
    first), then defer to the default hook so it still reaches stderr. Records the code
    path only — no local variables — so schedule data never leaks."""
    try:
        _rotate_log(ERROR_LOG)
        with ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} "
                    f"· v{APP_VERSION} · pid {os.getpid()} =====\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)

def install_crash_logging() -> None:
    """Wire up the two diagnostics as early as possible in main(). Best-effort: any
    failure here is swallowed so it can never keep the app from starting."""
    global _crash_fh
    try:
        _rotate_log(CRASH_LOG)
        _crash_fh = CRASH_LOG.open("a", encoding="utf-8")   # held open for the process life
        faulthandler.enable(file=_crash_fh, all_threads=True)
    except Exception:
        pass
    sys.excepthook = log_exception

# ── Local storage ──────────────────────────────────────────────────────────
def _migrate_types(acts: List[Dict]) -> List[Dict]:
    """Remap any legacy/unknown activity type onto the current set and refresh the
    block's colour to match the current palette. Runs silently on load."""
    by_id = {t["id"]: t for t in ACTIVITY_TYPES}
    for a in acts:
        tid = a.get("type")
        tid = _OLD_TYPE_MAP.get(tid, tid)
        if tid not in by_id:
            tid = "study"
        a["type"]  = tid
        a["color"] = by_id[tid]["color"]
    return acts

def load_all_activities() -> List[Dict]:
    try:
        return _migrate_types(json.loads(DATA_FILE.read_text()))
    except Exception:
        return []

def save_all_activities(acts: List[Dict]) -> None:
    try:
        # Rotate the outgoing state to .bak first. The dated daily backup below is
        # overwritten by every save, and the in-memory AI-undo stack dies with the
        # process — so without this, one bad save after a restart was unrecoverable.
        # .bak always lags the live file by exactly one save.
        if DATA_FILE.exists():
            shutil.copyfile(DATA_FILE, DATA_FILE.with_name("activities.json.bak"))
    except Exception:
        pass
    try:
        DATA_FILE.write_text(json.dumps(acts, indent=2))
        _write_daily_backup(acts)
    except Exception:
        pass

# ── Rolling daily backups ────────────────────────────────────────────────────
# Safety net against a bad edit / corrupt write, two layers:
#   activities.json.bak                — the state before the MOST RECENT save
#                                        (recovers a single bad save, even after
#                                        a restart wiped the in-memory AI undo)
#   backups/activities-YYYY-MM-DD.json — latest state per day, newest BACKUP_KEEP
#                                        kept (recovers across days)
BACKUP_DIR  = DATA_DIR / "backups"
BACKUP_KEEP = 14
BAK_FILE    = DATA_DIR / "activities.json.bak"
MANUAL_UNDO_KEEP = 24   # v4.0: Ctrl+Z for manual edits

def _write_daily_backup(acts: List[Dict]) -> None:
    """Best-effort: one snapshot per day (latest state of that day); prune to the
    newest BACKUP_KEEP. Never let a backup failure disrupt the real save."""
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        (BACKUP_DIR / f"activities-{date.today().isoformat()}.json").write_text(
            json.dumps(acts, indent=2))
        for old in sorted(BACKUP_DIR.glob("activities-*.json"))[:-BACKUP_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass

def list_schedule_backups() -> List[Dict]:
    """Discover restore points: .bak + dated dailies. Pure filesystem; no schedule
    contents loaded. Each item: {path, label, mtime, kind}."""
    out: List[Dict] = []
    try:
        if BAK_FILE.exists():
            st = BAK_FILE.stat()
            out.append({
                "path": BAK_FILE, "kind": "previous",
                "label": f"Previous save  ·  {datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')}",
                "mtime": st.st_mtime,
            })
    except OSError:
        pass
    try:
        for p in sorted(BACKUP_DIR.glob("activities-*.json"), reverse=True):
            try:
                st = p.stat()
                day = p.stem.replace("activities-", "", 1)
                out.append({
                    "path": p, "kind": "daily",
                    "label": f"Daily snapshot  ·  {day}  ·  {datetime.fromtimestamp(st.st_mtime).strftime('%H:%M')}",
                    "mtime": st.st_mtime,
                })
            except OSError:
                continue
    except OSError:
        pass
    out.sort(key=lambda x: -x["mtime"])
    return out

def load_activities_from_path(path: Path) -> Optional[List[Dict]]:
    """Load + migrate activities from a backup file. None if unreadable/invalid."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None
        return _migrate_types(data)
    except Exception:
        return None

def parse_calendar_ids(s: str) -> List[str]:
    """Comma-separated Google calendar IDs → non-empty list (default primary)."""
    ids = [x.strip() for x in str(s or "").split(",") if x.strip()]
    return ids or ["primary"]

# ── AI undo ──────────────────────────────────────────────────────────────────
# The assistant can rewrite or clear whole days, so snapshot the schedule before
# the first schedule-changing tool of each AI turn; "Undo" restores the snapshot.
AI_UNDO_KEEP      = 12
AI_READONLY_TOOLS = frozenset({"list_blocks", "find_free_time", "week_summary"})

# ── Notification de-dup (cross-process) ──────────────────────────────────────
# A block alert must fire EXACTLY ONCE per day — even if more than one copy of the
# app is running, each with its own 20 s notify timer (e.g. a second instance that
# slipped past the single-instance guard at boot, where Windows launches several
# copies at once). The in-memory `_notified` set only dedups within one process;
# these atomic marker files dedup ACROSS processes: os.open(O_CREAT|O_EXCL) lets
# exactly one claimer win the race, so two instances can no longer double-alert.
NOTIFY_MARK_DIR = DATA_DIR / ".notified"

def claim_block_alert(day: str, block_id: str, start_min: int) -> bool:
    """Atomically claim the right to alert for (day, block, start). Returns True for
    the first claimer across ALL processes, False if it was already claimed. Fails
    OPEN (True) on any filesystem error so a broken FS can't silence reminders."""
    try:
        NOTIFY_MARK_DIR.mkdir(parents=True, exist_ok=True)
        path = NOTIFY_MARK_DIR / f"{day}__{block_id}__{start_min}"
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return True

def purge_old_alert_marks(keep_day: str) -> None:
    """Drop alert markers from days other than `keep_day` so the dir can't grow."""
    try:
        for p in NOTIFY_MARK_DIR.glob("*"):
            if not p.name.startswith(keep_day + "__"):
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        pass

# ── Settings (persisted to ~/.daily-scheduler/settings.json) ────────────────
# Replaces the old behaviour where model / notify / DND reset to defaults every
# launch (only "Start with Windows" survived, via its Startup-folder .lnk).
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_SETTINGS = {
    "theme":            DEFAULT_THEME,
    "model":            DEFAULT_MODEL,
    "notify_on":        True,
    "notify_lead_min":  0,        # alert this many minutes before a block starts (0 = at start)
    "notify_end_chime": False,    # off by default — start alerts only
    "notify_sound":     True,     # play a tone with alerts (visual still shows)
    "notify_tone":      "chime",  # chime | soft | bright | low | glass
    "notify_volume":    80,       # 0–100
    "dnd_override":     True,
    "plan_day_start":   "08:00",  # default waking window the planner schedules within
    "plan_day_end":     "22:00",
    "ollama_autostart": False,    # keep Ollama off at launch unless the user opts in
    "ollama_models_dir": "",      # empty = Ollama default (~/.ollama/models); used when app starts Ollama
    "update_check_on":  True,     # check GitHub for a newer release on launch + daily
    "calendar_ids":     "primary",  # v4.0: comma-separated Google calendar IDs
    "body_split":       [],       # [calendar_px, sidebar_px, ai_px] — empty = defaults
    "sidebar_split":    [],       # [add_activity_px, summary_px] — empty = defaults
    "ai_panel_w":       340,      # remembered AI width when the panel is open
    "temperature":      0.3,
    "num_ctx":          16384,
    # Optional buffer: at Windows sign-in, wait this many seconds before building the
    # window. The real boot failures are fixed at the source (the `ollama list` hang in
    # ai.list_ollama_models, and the AMD GPU-crash-at-boot via disabling Fast Startup), so
    # this is now just a small settle buffer; raise it if a boot-time GPU reset ever
    # recurs, or set 0 to open the window immediately. Only applies to --startup.
    "startup_delay_sec": 5,
}

def load_settings() -> Dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        if isinstance(data, dict):
            s.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    except Exception:
        pass
    return s

def save_settings(s: Dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2))
    except Exception:
        pass

def parse_hhmm(s: str) -> int:
    """'18:30' / '6:30 pm' / '6 pm' / '24:00' → minutes from midnight.
    End-of-day is 1440 (DAY_END). QTime / strptime reject hour=24, so we accept
    the string form for AI tools and typed times. Raises ValueError on garbage."""
    s = (s or "").strip().lower().replace(".", "")
    if s in ("24:00", "24:0", "24", "2400"):
        return DAY_END
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%H"):
        try:
            t = datetime.strptime(s, fmt)
            return t.hour * 60 + t.minute
        except ValueError:
            continue
    raise ValueError(f"can't parse time '{s}' — use 24h HH:MM (or 24:00 for end of day)")

def coerce_end_min(sm: int, em: int, *, original_end: Optional[int] = None) -> int:
    """Map end-of-day conventions onto DAY_END (1440).
    QTime only holds 00:00–23:59, so End=00:00 with Start later the same day means
    through midnight (e.g. sleep 22:00–24:00). Start=End=00:00 stays zero-length
    unless this is a re-save of an existing full-day block (original_end was 1440)."""
    if em == 0 and sm > 0:
        return DAY_END
    # Re-edit of 00:00–24:00: both fields display as 00:00 — keep end-of-day.
    if em == 0 and sm == 0 and original_end is not None and int(original_end) >= DAY_END:
        return DAY_END
    return em

def end_alert_due(em: int, now_min: int, window: int = 2) -> bool:
    """True if a block ending at `em` should fire its end-alert at wall-clock `now_min`.
    `now_min` never reaches 1440 (max 23:59 = 1439), so endMin=DAY_END fires in the
    last `window` minutes of the day."""
    em = int(em); now_min = int(now_min); window = max(0, int(window))
    if em >= DAY_END:
        return now_min >= DAY_END - window
    return now_min - window <= em <= now_min

def start_alert_due(sm: int, now_min: int, lead: int = 0, window: int = 2) -> bool:
    """True if a block starting at `sm` should fire (optionally `lead` min early).
    Clamps fire time to ≥ 0 so early-morning blocks with lead still alert."""
    fire_at = max(0, int(sm) - max(0, int(lead)))
    now_min = int(now_min); window = max(0, int(window))
    return now_min - window <= fire_at <= now_min


_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}

def resolve_date(s, base: date) -> Optional[str]:
    """Resolve a date the model/user gave (relative to the viewed day `base`) to an
    ISO string. Accepts ISO, Month/Day ('6/14'), M/D/Y, today/tomorrow/yesterday,
    weekday names, or empty (=base). Returns None if it can't be understood.
    Keeps date math OUT of the model — it just passes through what the user said."""
    if s is None:
        return base.isoformat()
    t = str(s).strip().lower()
    if t in ("", "today", "viewed day", "the viewed day", "current day"):
        return base.isoformat()
    if t == "tomorrow":
        return (base + timedelta(days=1)).isoformat()
    if t == "yesterday":
        return (base - timedelta(days=1)).isoformat()
    if t in _WEEKDAYS:                                   # next occurrence after base
        delta = (_WEEKDAYS[t] - base.weekday()) % 7 or 7
        return (base + timedelta(days=delta)).isoformat()
    # Pull out month/day from any numeric form (M/D, M/D/Y, ISO yyyy-mm-dd) and IGNORE
    # the year — models often hallucinate it (e.g. 2023). This is a near-term planner,
    # so snap the month/day to whichever year puts it closest to the viewed day.
    nums = [int(n) for n in re.findall(r"\d+", t)]
    m = d = None
    if len(nums) >= 3 and nums[0] > 31:                  # ISO: year, month, day
        m, d = nums[1], nums[2]
    elif len(nums) >= 2:                                 # M/D or M/D/Y
        m, d = nums[0], nums[1]
    if m is None or d is None:
        return None
    cands = []
    for y in (base.year - 1, base.year, base.year + 1):
        try:
            cands.append(date(y, m, d))
        except ValueError:
            pass
    if not cands:
        return None
    return min(cands, key=lambda c: abs((c - base).days)).isoformat()

# ── Interval helpers ───────────────────────────────────────────────────────
def _merge(intervals):
    merged = []
    for oc in sorted(intervals, key=lambda x: x[0]):
        if merged and oc[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], oc[1]))
        else:
            merged.append(oc)
    return merged

def _free_slots(occupied_pairs, start=DAY_START, end=DAY_END):
    free, cur = [], start
    for s, e in _merge(occupied_pairs):
        if s > cur:
            free.append((cur, s))
        cur = max(cur, e)
    if cur < end:
        free.append((cur, end))
    return free

def _earliest_fit(occupied, cursor, length):
    """Earliest start >= cursor where a `length`-minute block fits without overlapping any
    `occupied` (s, e) interval, before DAY_END. None if it won't fit. Used by plan_day to
    flow chunked tasks past fixed anchors and meetings."""
    for s, e in _free_slots(occupied, cursor, DAY_END):
        if e - s >= length:
            return s
    return None

def norm_title(s: str) -> str:
    """Normalize a title for fuzzy matching: lowercase, alphanumerics + spaces only
    (strips emoji/punctuation so 'gym' matches '🏋 Gym Session')."""
    return " ".join("".join(ch for ch in str(s).lower()
                            if ch.isalnum() or ch.isspace()).split())


def find_free_placement(day_blocks: List[Dict], want_start: int, dur: int) -> Optional[int]:
    """Start time closest to want_start where a dur-minute block fits without
    overlapping anything. None if no gap that size exists in the day."""
    occ  = _merge([(b["startMin"], b["endMin"]) for b in day_blocks])
    best = None
    for s, e in _free_slots(occ):
        if e - s < dur:
            continue
        cand  = min(max(want_start, s), e - dur)
        score = abs(cand - want_start)
        if best is None or score < best[0]:
            best = (score, cand)
    return None if best is None else best[1]


def sequentialize(blocks: List[Dict], blocked=None) -> tuple:
    """Sort by start time and push overlapping blocks later until the plan is
    conflict-free. If `blocked` intervals are given (e.g. read-only calendar events),
    editable blocks are also pushed out of those windows so they never land on a
    meeting. Gaps are preserved; blocks pushed past the end of day are dropped.
    Returns (kept_blocks, n_adjusted, n_dropped)."""
    blocked = sorted(blocked or [])
    out, adjusted, dropped = [], 0, 0
    cur = DAY_START
    for b in sorted(blocks, key=lambda x: (x["startMin"], x["endMin"])):
        dur = b["endMin"] - b["startMin"]
        ns  = max(b["startMin"], cur)
        # Step past any calendar window this block would overlap; moving past one
        # window can push it into the next, so repeat until it sits in the clear.
        moved = True
        while moved:
            moved = False
            for bs, be in blocked:
                if ns < be and ns + dur > bs:
                    ns = be; moved = True
        if ns + dur > DAY_END:
            dropped += 1
            continue
        if ns != b["startMin"]:
            adjusted += 1
        out.append({**b, "startMin": ns, "endMin": ns + dur})
        cur = ns + dur
    return out, adjusted, dropped

def assign_overlap_cols(blocks: List[Dict]) -> List[Dict]:
    """Greedy column assignment for time-overlapping blocks (side-by-side layout).
    Returns copies with `_col` (column index) and `_tcols` (total columns among the
    blocks it overlaps). Input must be sorted by startMin. Shared by the Day
    timeline and the Week view so overlapping blocks render identically."""
    col_ends, result = [], []
    for blk in blocks:
        col = next((i for i, e in enumerate(col_ends) if e <= blk["startMin"]), len(col_ends))
        if col == len(col_ends):
            col_ends.append(0)
        col_ends[col] = blk["endMin"]
        result.append({**blk, "_col": col})
    for i, blk in enumerate(result):
        cols = [blk["_col"]] + [
            b["_col"] for j, b in enumerate(result)
            if j != i and b["startMin"] < blk["endMin"] and b["endMin"] > blk["startMin"]
        ]
        result[i]["_tcols"] = max(cols) + 1
    return result
