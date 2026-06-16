#!/usr/bin/env python3
"""
Daily Scheduler — Native Desktop App
Pure Python + PySide6 (Qt6). No browser engine.
"""

import sys
import json
import threading
import uuid
import shutil
import platform
import subprocess
import re
import calendar as _cal
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict

import requests
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QScrollArea, QFrame,
    QDialog, QFileDialog, QTimeEdit, QStackedWidget, QSizePolicy,
    QMessageBox, QMenu, QGridLayout, QProgressBar, QSystemTrayIcon,
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QRect, QSize, QTime,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics,
    QPainterPath, QPalette, QCursor, QAction, QPixmap, QIcon,
)

# ── App data paths ─────────────────────────────────────────────────────────
DATA_DIR   = Path.home() / ".daily-scheduler"
DATA_FILE  = DATA_DIR / "activities.json"
CREDS_FILE = DATA_DIR / "credentials.json"
TOKEN_FILE = DATA_DIR / "token.json"
DATA_DIR.mkdir(exist_ok=True)

# ── Layout constants ───────────────────────────────────────────────────────
DAY_START_H = 7
DAY_END_H   = 23
DAY_START   = DAY_START_H * 60   # minutes from midnight
DAY_END     = DAY_END_H   * 60
HOUR_PX     = 96                  # pixels per hour on timeline
GUTTER_W    = 64                  # width of time-label column
OLLAMA_URL  = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:14b"     # better at tool-use/reasoning than llama3.1:8b

# ── Colour palette (matches original dark theme) ───────────────────────────
C_BG      = QColor("#0d0d14")
C_SURFACE = QColor("#15151f")
C_SURF2   = QColor("#1c1c2a")
C_BORDER  = QColor("#262636")
C_BORDER2 = QColor("#2f2f45")
C_TEXT    = QColor("#e4e4f0")
C_MUTED   = QColor("#7a7a9a")
C_ACCENT  = QColor("#7c6ff7")
C_ACCENT2 = QColor("#5c56c4")
C_NOW     = QColor("#f43f5e")

# ── Activity types ─────────────────────────────────────────────────────────
ACTIVITY_TYPES = [
    {"id": "study",   "label": "Study",          "icon": "📚", "color": "#8b5cf6"},
    {"id": "project", "label": "Project",         "icon": "🛠",  "color": "#f59e0b"},
    {"id": "extra",   "label": "Extracurricular", "icon": "🎯", "color": "#ec4899"},
    {"id": "gaming",  "label": "Gaming",          "icon": "🎮", "color": "#ef4444"},
    {"id": "anime",   "label": "Anime",           "icon": "🍥", "color": "#06b6d4"},
    {"id": "friends", "label": "Friends",         "icon": "📞", "color": "#f97316"},
]

# ── Pure helper functions ──────────────────────────────────────────────────
def min_to_y(minutes: int) -> int:
    return int((minutes - DAY_START) / 60 * HOUR_PX)

def y_to_min(y: int) -> int:
    return int(DAY_START + y / HOUR_PX * 60)

def fmt_time(minutes: int) -> str:
    h, m = divmod(minutes % (24 * 60), 60)
    ap = "pm" if h >= 12 else "am"
    return f"{h % 12 or 12}:{m:02d} {ap}"

def fmt_dur(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"

def today_str() -> str:
    return date.today().isoformat()

def new_id() -> str:
    return str(uuid.uuid4())[:8]

def qcolor(hex_str: str) -> QColor:
    return QColor(hex_str)

# ── Local storage ──────────────────────────────────────────────────────────
def load_all_activities() -> List[Dict]:
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception:
        return []

def save_all_activities(acts: List[Dict]) -> None:
    try:
        DATA_FILE.write_text(json.dumps(acts, indent=2))
    except Exception:
        pass

def parse_hhmm(s: str) -> int:
    """'18:30' / '6:30 pm' / '6 pm' → minutes from midnight. Raises ValueError."""
    s = (s or "").strip().lower().replace(".", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%H"):
        try:
            t = datetime.strptime(s, fmt)
            return t.hour * 60 + t.minute
        except ValueError:
            continue
    raise ValueError(f"can't parse time '{s}' — use 24h HH:MM")


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


def sequentialize(blocks: List[Dict]) -> tuple:
    """Sort by start time and push overlapping blocks later until the plan is
    conflict-free. Gaps are preserved; blocks pushed past the end of day are
    dropped. Returns (kept_blocks, n_adjusted, n_dropped)."""
    out, adjusted, dropped = [], 0, 0
    cur = DAY_START
    for b in sorted(blocks, key=lambda x: (x["startMin"], x["endMin"])):
        dur = b["endMin"] - b["startMin"]
        ns  = max(b["startMin"], cur)
        if ns + dur > DAY_END:
            dropped += 1
            continue
        if ns != b["startMin"]:
            adjusted += 1
        out.append({**b, "startMin": ns, "endMin": ns + dur})
        cur = ns + dur
    return out, adjusted, dropped

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
            if TOKEN_FILE.exists():
                try:
                    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
                except Exception:
                    pass

            if creds and creds.valid:
                self.done.emit(creds); return

            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    TOKEN_FILE.write_text(creds.to_json())
                    self.done.emit(creds); return
                except Exception:
                    pass

            if not CREDS_FILE.exists():
                self.error.emit(
                    "credentials.json not found.\n\n"
                    "Download it from Google Cloud Console:\n"
                    "APIs & Services → Credentials → OAuth 2.0 Client ID\n"
                    "(choose Desktop application) → Download JSON\n"
                    "then load it from the setup screen."
                ); return

            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
            TOKEN_FILE.write_text(creds.to_json())
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
    error = Signal(str)

    def __init__(self, creds, start: date, end: date):
        super().__init__()
        self.creds = creds
        self.start = start
        self.end   = end    # exclusive

    def run(self):
        try:
            from googleapiclient.discovery import build
            svc = build("calendar", "v3", credentials=self.creds)
            t0  = datetime.combine(self.start, datetime.min.time()).astimezone()
            t1  = datetime.combine(self.end,   datetime.min.time()).astimezone()
            by_date: Dict[str, List[Dict]] = {}
            page = None
            while True:
                res = svc.events().list(
                    calendarId="primary",
                    timeMin=t0.isoformat(), timeMax=t1.isoformat(),
                    singleEvents=True, orderBy="startTime",
                    maxResults=2500, pageToken=page,
                ).execute()
                for ev in res.get("items", []):
                    s_raw = ev.get("start", {}).get("dateTime")
                    e_raw = ev.get("end",   {}).get("dateTime")
                    if not s_raw:
                        continue   # skip all-day events
                    s  = datetime.fromisoformat(s_raw.replace("Z", "+00:00")).astimezone()
                    en = datetime.fromisoformat(e_raw.replace("Z", "+00:00")).astimezone()
                    sm = max(s.hour*60 + s.minute, DAY_START)
                    em = min(en.hour*60 + en.minute, DAY_END)
                    if em <= sm:
                        continue
                    by_date.setdefault(s.date().isoformat(), []).append({
                        "id": ev.get("id", new_id()), "title": ev.get("summary", "(no title)"),
                        "startMin": sm, "endMin": em, "type": "calendar",
                        "color": "#3b82f6", "date": s.date().isoformat(),
                    })
                page = res.get("nextPageToken")
                if not page:
                    break
            self.done.emit(by_date)
        except Exception as ex:
            self.error.emit(str(ex))

# ── Ollama shutdown ────────────────────────────────────────────────────────
def stop_ollama():
    """Fully stop local Ollama: the tray app, the server, AND the model-runner child
    (llama-server) that actually holds the VRAM. Killing only ollama.exe orphans the
    runner and leaks GPU memory, so the runner images are killed explicitly.
    Returns (ok, message)."""
    try:
        if platform.system() == "Windows":
            NO_WIN = 0x08000000  # CREATE_NO_WINDOW — no console flash
            killed = False
            # Coordinator first, then the runner(s) that pin VRAM. /T also takes any
            # still-attached children. Runner is named "llama-server.exe" on current
            # Ollama; older builds used "ollama_llama_server.exe".
            for image in ("ollama app.exe", "ollama.exe",
                          "llama-server.exe", "ollama_llama_server.exe"):
                r = subprocess.run(
                    ["taskkill", "/F", "/T", "/IM", image],
                    capture_output=True, text=True, creationflags=NO_WIN,
                )
                if "SUCCESS" in (r.stdout or ""):
                    killed = True
            return (True, "Ollama stopped.") if killed else (False, "Ollama wasn't running.")
        else:
            a = subprocess.run(["pkill", "-f", "ollama"], capture_output=True, text=True)
            subprocess.run(["pkill", "-f", "llama-server"], capture_output=True, text=True)
            return (a.returncode == 0,
                    "Ollama stopped." if a.returncode == 0 else "Ollama wasn't running.")
    except Exception as ex:
        return False, str(ex)


def start_ollama():
    """Launch the local Ollama server (detached). Returns (ok, message)."""
    try:
        if platform.system() == "Windows":
            DETACHED = 0x00000008  # DETACHED_PROCESS
            NO_WIN   = 0x08000000  # CREATE_NO_WINDOW
            subprocess.Popen(
                ["ollama", "serve"],
                creationflags=DETACHED | NO_WIN,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return True, "Starting Ollama…"
    except FileNotFoundError:
        return False, "Ollama not found on PATH.\nInstall it from https://ollama.com/download"
    except Exception as ex:
        return False, str(ex)


# ── Run-at-login (Windows) ──────────────────────────────────────────────────
_RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "DailyScheduler"

def _startup_command() -> str:
    """Command Windows should run at login. Launches the APP only (never Ollama),
    with --startup so it opens quietly into the tray."""
    if getattr(sys, "frozen", False):                 # packaged .exe
        return f'"{sys.executable}" --startup'
    return f'"{sys.executable}" "{Path(__file__).resolve()}" --startup'   # source

def is_startup_enabled() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _RUN_NAME)
            return bool(val)
    except OSError:
        return False

def set_startup(enabled: bool) -> bool:
    """Add/remove the HKCU Run entry. No admin rights needed. Returns success."""
    if platform.system() != "Windows":
        return False
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, _startup_command())
            else:
                try:
                    winreg.DeleteValue(k, _RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def unload_ollama_model(model):
    """Unload a model from memory but keep the Ollama server running.
    Uses keep_alive=0, the documented way to free VRAM/RAM immediately."""
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": 0}, timeout=10,
        )
        if r.ok:
            return True, f"Unloaded '{model}' from memory."
        return False, f"Ollama returned status {r.status_code}."
    except requests.exceptions.ConnectionError:
        return False, "Ollama isn't running."
    except Exception as ex:
        return False, str(ex)


# ── Ollama streaming thread ────────────────────────────────────────────────
class OllamaCheckThread(QThread):
    result = Signal(bool)
    def run(self):
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            self.result.emit(r.ok)
        except Exception:
            self.result.emit(False)


class OllamaThread(QThread):
    token      = Signal(str)
    done       = Signal()
    tool_calls = Signal(list)
    error      = Signal(str)

    def __init__(self, messages, model, tools=None):
        super().__init__()
        self.messages = messages
        self.model    = model
        self.tools    = tools
        self._stop    = False

    def stop(self): self._stop = True

    def run(self):
        try:
            payload = {"model": self.model, "messages": self.messages, "stream": True,
                       "options": {"num_ctx": 16384, "temperature": 0.3, "top_p": 0.9}}
            if self.tools:
                payload["tools"] = self.tools
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat", json=payload,
                stream=True, timeout=120,
            )
            # 404 here almost always means "model not installed" — translate it.
            if resp.status_code == 404:
                err = ""
                try:
                    err = resp.json().get("error", "")
                except Exception:
                    pass
                self.error.emit(
                    f"Model '{self.model}' isn't installed.\n\n"
                    f"Pull it from a terminal:\n    ollama pull {self.model}\n\n"
                    f"Or type a model you already have into the Model field above."
                    + (f"\n\n(Ollama said: {err})" if err else "")
                )
                return
            resp.raise_for_status()
            calls = []
            for line in resp.iter_lines():
                if self._stop: break
                if not line: continue
                try:
                    data = json.loads(line)
                    msg  = data.get("message") or {}
                    c    = msg.get("content", "")
                    if c: self.token.emit(c)
                    if msg.get("tool_calls"):
                        calls.extend(msg["tool_calls"])
                    if data.get("done"): break
                except Exception:
                    pass
            if calls and not self._stop:
                self.tool_calls.emit(calls)
            else:
                self.done.emit()
        except requests.exceptions.ConnectionError:
            self.error.emit("Can't reach Ollama. Click the ▶ button to start it,\n"
                            "or run 'ollama serve' in a terminal.")
        except Exception as ex:
            self.error.emit(str(ex))

# ── AI tools — let the model edit the schedule directly ────────────────────
AI_TOOLS = [
    {"type": "function", "function": {
        "name": "add_block",
        "description": "Add a block to the user's schedule. Times are 24-hour HH:MM.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the currently viewed day."},
            "start": {"type": "string", "description": "Start time, 24h HH:MM"},
            "end":   {"type": "string", "description": "End time, 24h HH:MM"},
            "title": {"type": "string", "description": "Short title for the block"},
            "type":  {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES],
                       "description": "Activity category"},
        }, "required": ["start", "end", "title"]}}},
    {"type": "function", "function": {
        "name": "delete_block",
        "description": "Delete user-created block(s) whose title contains the given text, on a date.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "title": {"type": "string", "description": "Title (or part of it) of the block to delete"},
        }, "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "move_block",
        "description": "Move or resize a user-created block (matched by title) to new times and/or a new date.",
        "parameters": {"type": "object", "properties": {
            "date":     {"type": "string", "description": "Date the block is currently on (YYYY-MM-DD). Omit for the viewed day."},
            "title":    {"type": "string", "description": "Title (or part) of the block to move"},
            "start":    {"type": "string", "description": "New start time 24h HH:MM"},
            "end":      {"type": "string", "description": "New end time 24h HH:MM"},
            "new_date": {"type": "string", "description": "New date YYYY-MM-DD if moving to another day"},
            "new_title": {"type": "string", "description": "New title, to rename the block"},
        }, "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "list_blocks",
        "description": "List everything on the schedule for a date.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
        }}}},
    {"type": "function", "function": {
        "name": "clear_day",
        "description": "Delete ALL editable blocks on a date (wipe the day's plan) in one call.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
        }}}},
    {"type": "function", "function": {
        "name": "shift_blocks",
        "description": "Shift EVERY editable block on a date by one offset. Use this single call to move a whole day — never move blocks one at a time for this.",
        "parameters": {"type": "object", "properties": {
            "date":    {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "minutes": {"type": "integer", "description": "Offset in minutes. Positive = later, negative = earlier (120 = 2 hours later)."},
        }, "required": ["minutes"]}}},
    {"type": "function", "function": {
        "name": "replace_day",
        "description": "Replace the ENTIRE set of editable blocks on a date with a new plan, in one atomic call. Best way to restructure a day, split work into chunks, or build a plan with breaks. IMPORTANT: this DELETES every existing block not in your list — if the user wants to keep other blocks, include them in 'blocks' too.",
        "parameters": {"type": "object", "properties": {
            "date":   {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "blocks": {"type": "array", "description": "Complete new plan for the day, in time order.",
                "items": {"type": "object", "properties": {
                    "start": {"type": "string", "description": "24h HH:MM"},
                    "end":   {"type": "string", "description": "24h HH:MM"},
                    "title": {"type": "string"},
                    "type":  {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
                }, "required": ["start", "end", "title"]}},
        }, "required": ["blocks"]}}},
    {"type": "function", "function": {
        "name": "copy_day",
        "description": "Copy ALL editable blocks from one date to another in one call. "
                       "Use this for 'copy/duplicate my schedule to <day>'. By default it "
                       "REPLACES the target day's blocks with the copies.",
        "parameters": {"type": "object", "properties": {
            "from_date": {"type": "string", "description": "Source date YYYY-MM-DD (omit = viewed day)."},
            "to_date":   {"type": "string", "description": "Target date YYYY-MM-DD."},
            "merge":     {"type": "boolean", "description": "If true, keep the target's existing blocks and add the copies alongside them. Default false (replace)."},
        }, "required": ["to_date"]}}},
]

AI_TOOL_NAMES = {t["function"]["name"] for t in AI_TOOLS}

# How many tool-call rounds the model may take in one turn. High enough for
# edit → verify (list_blocks) → fix → re-verify cycles, capped to avoid runaway loops.
MAX_TOOL_ROUNDS = 8


def _json_spans(s: str):
    """Yield balanced {...} / [...] substrings (brace-depth aware, handles nesting)."""
    depth, start = 0, None
    for i, ch in enumerate(s):
        if ch in "{[":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "}]" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield s[start:i + 1]
                start = None


def looks_like_tool_text(s: str) -> bool:
    """Heuristic: is this streamed content actually a tool call printed as text?"""
    t = s.lstrip()
    return (t.startswith("{") or t.startswith("[")
            or t.startswith("<|python_tag|>") or t.startswith("```")
            or "<|python_tag|>" in s[:40])


def extract_tool_calls(text: str):
    """Recover tool calls a model printed as content text instead of using the
    native tool_calls channel. Handles <|python_tag|>, ``` fences, bare objects,
    JSON arrays, and {type:function, function:{...}} / {name, arguments|parameters}
    shapes. Returns a list of {"name", "args"} for known tools only."""
    if not text:
        return []
    s = text.replace("<|python_tag|>", " ").replace("<|eom_id|>", " ")
    for fence in ("```json", "```tool_code", "```python", "```tool_call", "```"):
        s = s.replace(fence, " ")
    found = []
    for span in _json_spans(s):
        try:
            obj = json.loads(span)
        except Exception:
            continue
        for it in (obj if isinstance(obj, list) else [obj]):
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("function"), dict):   # {type:function, function:{...}}
                it = it["function"]
            name = it.get("name")
            if name not in AI_TOOL_NAMES:
                continue
            args = it.get("arguments")
            if args is None:
                args = it.get("parameters", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            found.append({"name": name, "args": args if isinstance(args, dict) else {}})
    return found

AI_GREETING = (
    "Hey! I'm your scheduling assistant — I can see your calendar and edit it directly. "
    "Try things like:\n\n"
    "  •  \"Add a study block from 2 to 4pm\"\n"
    "  •  \"Shift everything 2 hours later\"\n"
    "  •  \"Clear out tomorrow\"\n"
    "  •  \"Replan my afternoon: 2h of AP work in 30-min chunks with breaks\"\n\n"
    "What would you like to do with your day?"
)

# ══════════════════════════════════════════════════════════════════════════
#  TIMELINE WIDGET  (custom-painted — pure Qt, no browser)
# ══════════════════════════════════════════════════════════════════════════
class TimelineWidget(QWidget):
    block_create_req    = Signal(int, int)   # start_min, end_min — drag/click to create
    activity_delete_req = Signal(str)        # activity id
    activity_edit_req   = Signal(str)        # activity id — open the edit dialog
    activity_changed    = Signal(str, int, int)  # id, new_start, new_end (drag move/resize)

    SNAP   = 15   # minutes — selection snaps to this grid (like Google Calendar)
    EDGE_PX = 7   # pixels near a block's top/bottom that trigger resize

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cal_events:  List[Dict] = []
        self.activities:  List[Dict] = []
        self._hover_min:  Optional[int]   = None   # snapped minute under cursor
        self._drag_start: Optional[int]   = None   # snapped minute where create-drag began
        self._drag_cur:   Optional[int]   = None   # snapped minute under cursor while creating
        # move / resize of an existing user block
        self._edit_id:    Optional[str]   = None
        self._edit_mode:  Optional[str]   = None   # "move" | "resize_top" | "resize_bottom"
        self._edit_orig:  Optional[tuple] = None   # (start, end) at press
        self._press_min:  Optional[int]   = None   # unsnapped minute at press
        self._preview:    Optional[tuple] = None   # (id, start, end) live during drag
        self._moved:      bool            = False  # did the cursor actually move?
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(min_to_y(DAY_END) + 24)

    def _snap(self, minute: int) -> int:
        m = round(minute / self.SNAP) * self.SNAP
        return max(DAY_START, min(DAY_END, m))

    def set_data(self, cal, acts, view_date=None):
        self.cal_events = cal
        self.activities = acts
        self.view_date  = view_date or date.today()
        self.update()

    # ── helpers ────────────────────────────────────────────────────────────
    def _all_blocks(self):
        return sorted(
            [{"_btype": "calendar", **e} for e in self.cal_events] +
            [{"_btype": "user",     **e} for e in self.activities],
            key=lambda x: x["startMin"],
        )

    def _free_intervals(self):
        occ = [(b["startMin"], b["endMin"]) for b in self._all_blocks()]
        return _free_slots(occ)

    def _assign_cols(self, blocks):
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

    # ── painting ───────────────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), C_BG)
        self._draw_grid(p)
        self._draw_free(p)
        self._draw_events(p)
        self._draw_drag(p)
        self._draw_now(p)

    def _draw_grid(self, p: QPainter):
        lbl_font = QFont("Segoe UI", 8)
        p.setFont(lbl_font)
        for h in range(DAY_START_H, DAY_END_H + 1):
            y = min_to_y(h * 60)
            p.setPen(QPen(C_BORDER, 1))
            p.drawLine(GUTTER_W, y, self.width(), y)
            if h < DAY_END_H:
                yh = min_to_y(h * 60 + 30)
                pen = QPen(QColor("#1e1e2e"), 1, Qt.DashLine)
                p.setPen(pen)
                p.drawLine(GUTTER_W, yh, self.width(), yh)
            lbl = "12 pm" if h == 12 else (f"{h} am" if h < 12 else f"{h-12} pm")
            p.setPen(C_MUTED)
            p.drawText(QRect(0, y - 8, GUTTER_W - 6, 18),
                       Qt.AlignRight | Qt.AlignVCenter, lbl)

    def _draw_free(self, p: QPainter):
        # Subtle highlight of the free interval under the cursor (only when not dragging)
        if self._drag_start is not None or self._hover_min is None:
            return
        for s, e in self._free_intervals():
            if not (s <= self._hover_min <= e):
                continue
            dur = e - s
            if dur < 5:
                return
            y = min_to_y(s)
            h = max(min_to_y(e) - y, 12)
            x = GUTTER_W + 4
            w = self.width() - GUTTER_W - 8
            rect = QRect(x, y, w, h)
            p.fillRect(rect, QColor(124, 111, 247, 14))
            p.setPen(QPen(QColor(124, 111, 247, 70), 1, Qt.DashLine))
            p.drawRect(rect)
            if dur >= 20:
                p.setPen(QColor(124, 111, 247, 170))
                p.setFont(QFont("Segoe UI", 9))
                p.drawText(rect.adjusted(10, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           "＋ drag to create, or click")
            return

    def _draw_drag(self, p: QPainter):
        if self._drag_start is None or self._drag_cur is None:
            return
        s, e = sorted((self._drag_start, self._drag_cur))
        if e - s < self.SNAP:
            e = s + self.SNAP  # always show at least one snap-cell while dragging
        y = min_to_y(s)
        h = max(min_to_y(e) - y, 6)
        x = GUTTER_W + 4
        w = self.width() - GUTTER_W - 8
        rect = QRect(x, y, w, h)
        p.fillRect(rect, QColor(124, 111, 247, 60))
        p.setPen(QPen(C_ACCENT, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect)
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI", 9, QFont.Bold))
        p.drawText(rect.adjusted(10, 4, -8, -4), Qt.AlignTop | Qt.AlignLeft,
                   f"{fmt_time(s)} – {fmt_time(e)}  ·  {fmt_dur(e - s)}")

    def _layout_blocks(self):
        """Return [(block, QRect)] for every block, using committed times.
        Shared by painting and mouse hit-testing so they always agree."""
        area_w = self.width() - GUTTER_W - 8
        out = []
        for blk in self._assign_cols(self._all_blocks()):
            y  = min_to_y(blk["startMin"])
            h  = max(min_to_y(blk["endMin"]) - y, 20)
            cw = area_w / blk["_tcols"]
            x  = int(GUTTER_W + 4 + blk["_col"] * cw)
            w  = int(cw - 4)
            out.append((blk, QRect(x, y, w, h)))
        return out

    def _user_block_at(self, x: int, y: int):
        """Topmost user (editable) block whose rect contains (x, y), or None."""
        hit = None
        for blk, rect in self._layout_blocks():
            if blk.get("_btype") == "user" and rect.contains(int(x), int(y)):
                hit = (blk, rect)   # later (higher column) blocks win
        return hit

    def _draw_events(self, p: QPainter):
        fn_bold  = QFont("Segoe UI", 9, QFont.Bold)
        fn_small = QFont("Segoe UI", 8)

        for blk, rect in self._layout_blocks():
            # apply live drag preview to the block being moved/resized
            if self._preview and blk.get("_btype") == "user" and blk["id"] == self._preview[0]:
                ps, pe = self._preview[1], self._preview[2]
                y = min_to_y(ps); h = max(min_to_y(pe) - y, 20)
                rect = QRect(rect.x(), y, rect.width(), h)
                blk  = {**blk, "startMin": ps, "endMin": pe}

            dur  = blk["endMin"] - blk["startMin"]
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

            c    = QColor(blk.get("color", "#7c6ff7"))
            bg   = QColor(c.red(), c.green(), c.blue(), 45)

            p.fillRect(rect, bg)
            p.fillRect(QRect(x, y, 3, h), c)
            # highlight the block currently being dragged
            if self._preview and blk.get("_btype") == "user" and blk["id"] == self._preview[0]:
                p.setPen(QPen(c, 1.5)); p.setBrush(Qt.NoBrush); p.drawRect(rect)

            tr = rect.adjusted(8, 4, -4, -4)
            if dur >= 25:
                p.setFont(fn_bold); p.setPen(c)
                p.drawText(tr, Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap, blk["title"])
                if dur >= 40:
                    p.setFont(fn_small)
                    p.setPen(QColor(c.red(), c.green(), c.blue(), 170))
                    fm_h = QFontMetrics(fn_bold).height()
                    sub  = QRect(tr.left(), tr.top() + fm_h + 2, tr.width(), tr.height())
                    p.drawText(sub, Qt.AlignTop | Qt.AlignLeft,
                               f"{fmt_time(blk['startMin'])} – {fmt_time(blk['endMin'])}  ·  {fmt_dur(dur)}")
            else:
                p.setFont(fn_small); p.setPen(c)
                p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft, blk["title"])

    def _draw_now(self, p: QPainter):
        if getattr(self, "view_date", date.today()) != date.today():
            return
        now = datetime.now()
        nm  = now.hour * 60 + now.minute
        if not (DAY_START <= nm <= DAY_END):
            return
        y = min_to_y(nm)
        p.setPen(Qt.NoPen); p.setBrush(C_NOW)
        p.drawEllipse(GUTTER_W - 5, y - 4, 9, 9)
        p.setPen(QPen(C_NOW, 2)); p.setBrush(Qt.NoBrush)
        p.drawLine(GUTTER_W + 4, y, self.width(), y)

    # ── mouse ──────────────────────────────────────────────────────────────
    def _free_at(self, y: int):
        m = y_to_min(y)
        for s, e in self._free_intervals():
            if s <= m <= e:
                return (s, e)
        return None

    def _edit_mode_for(self, rect: QRect, y: int) -> str:
        """Resize if near a tall-enough block's top/bottom edge, else move."""
        if rect.height() >= 2 * self.EDGE_PX + 6:
            if y - rect.top() <= self.EDGE_PX:
                return "resize_top"
            if rect.bottom() - y <= self.EDGE_PX:
                return "resize_bottom"
        return "move"

    def mouseMoveEvent(self, ev):
        x = ev.position().x() if hasattr(ev, "position") else ev.x()
        y = int(ev.position().y()) if hasattr(ev, "position") else ev.y()

        # ── live move / resize of an existing block ─────────────────────────
        if self._edit_mode:
            self._moved = True
            delta = y_to_min(y) - self._press_min
            os_, oe = self._edit_orig
            dur = oe - os_
            if self._edit_mode == "move":
                ns = self._snap(os_ + delta)
                ns = max(DAY_START, min(ns, DAY_END - dur))
                self._preview = (self._edit_id, ns, ns + dur)
            elif self._edit_mode == "resize_top":
                ns = self._snap(os_ + delta)
                ns = max(DAY_START, min(ns, oe - self.SNAP))
                self._preview = (self._edit_id, ns, oe)
            else:  # resize_bottom
                ne = self._snap(oe + delta)
                ne = min(DAY_END, max(ne, os_ + self.SNAP))
                self._preview = (self._edit_id, os_, ne)
            self.update()
            return

        if x < GUTTER_W and self._drag_start is None:
            if self._hover_min is not None:
                self._hover_min = None; self.update()
            self.setCursor(Qt.ArrowCursor); return

        # ── creating a new block by dragging empty space ────────────────────
        if self._drag_start is not None:
            self._drag_cur = self._snap(y_to_min(y))
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return

        # ── hover feedback: resize cursor on edges, hand over blocks ────────
        hit = self._user_block_at(x, y)
        if hit:
            mode = self._edit_mode_for(hit[1], y)
            self.setCursor(Qt.SizeVerCursor if mode.startswith("resize")
                           else Qt.OpenHandCursor)
            if self._hover_min is not None:
                self._hover_min = None; self.update()
        else:
            snapped = self._snap(y_to_min(y))
            self.setCursor(Qt.PointingHandCursor)
            if snapped != self._hover_min:
                self._hover_min = snapped
                self.update()

    def mousePressEvent(self, ev):
        x = ev.position().x() if hasattr(ev, "position") else ev.x()
        y = int(ev.position().y()) if hasattr(ev, "position") else ev.y()
        if ev.button() != Qt.LeftButton or x < GUTTER_W:
            return
        hit = self._user_block_at(x, y)
        if hit:
            # start a move / resize on the existing block (a no-move release = edit)
            blk, rect = hit
            self._edit_id   = blk["id"]
            self._edit_mode = self._edit_mode_for(rect, y)
            self._edit_orig = (blk["startMin"], blk["endMin"])
            self._press_min = y_to_min(y)
            self._preview   = (blk["id"], blk["startMin"], blk["endMin"])
            self._moved     = False
            self.update()
            return
        # otherwise begin creating a block
        self._drag_start = self._snap(y_to_min(y))
        self._drag_cur   = self._drag_start
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return

        # ── finish a move / resize (or treat a no-move click as "edit") ─────
        if self._edit_mode:
            aid = self._edit_id
            preview, moved, orig = self._preview, self._moved, self._edit_orig
            self._edit_mode = self._edit_id = self._edit_orig = None
            self._press_min = self._preview = None
            self.update()
            if moved and preview and (preview[1], preview[2]) != orig:
                self.activity_changed.emit(aid, preview[1], preview[2])
            else:
                self.activity_edit_req.emit(aid)   # a plain click → open editor
            return

        # ── finish creating a block ─────────────────────────────────────────
        if self._drag_start is None:
            return
        s, e = sorted((self._drag_start, self._drag_cur))
        self._drag_start = self._drag_cur = None
        self.update()
        if e - s >= self.SNAP:
            self.block_create_req.emit(s, e)
        else:
            occ = sorted((b["startMin"], b["endMin"]) for b in self._all_blocks())
            end = min(s + 60, DAY_END)
            for os_, oe in occ:
                if os_ >= e and os_ < end:
                    end = os_
                    break
            if end - s >= self.SNAP:
                self.block_create_req.emit(s, end)

    def contextMenuEvent(self, ev):
        x = ev.x(); y = ev.y()
        if x < GUTTER_W:
            return
        hit = self._user_block_at(x, y)
        if not hit:
            return
        act = hit[0]
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()};
                     border: 1px solid {C_BORDER2.name()}; padding: 4px; }}
            QMenu::item {{ padding: 6px 14px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {C_SURF2.name()}; }}
        """)
        edit_act = menu.addAction(f"✏  Edit '{act['title']}'…")
        del_act  = menu.addAction(f"🗑  Delete '{act['title']}'")
        chosen = menu.exec(ev.globalPos())
        if chosen == edit_act:
            self.activity_edit_req.emit(act["id"])
        elif chosen == del_act:
            self.activity_delete_req.emit(act["id"])

    def leaveEvent(self, _ev):
        if self._drag_start is None and self._edit_mode is None:
            self._hover_min = None
            self.update()

# ══════════════════════════════════════════════════════════════════════════
#  ADD ACTIVITY DIALOG
# ══════════════════════════════════════════════════════════════════════════
class AddActivityDialog(QDialog):
    def __init__(self, start_min, end_min, sel_type, for_date=None,
                 existing=None, parent=None):
        super().__init__(parent)
        self._existing = existing
        is_edit = existing is not None
        if is_edit:
            sel_type  = existing.get("type", sel_type)
            start_min = existing["startMin"]
            end_min   = existing["endMin"]
            for_date  = existing.get("date", for_date)
        self.setWindowTitle("Edit Activity" if is_edit else "Add Activity")
        self.setFixedWidth(380)
        self.result_activity = None
        self.result_deleted  = False
        self._sel = sel_type
        self._for_date = for_date or today_str()

        self.setStyleSheet(f"""
            QDialog   {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()}; }}
            QLabel    {{ background: transparent; color: {C_TEXT.name()}; }}
            QTimeEdit, QLineEdit {{
                background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_TEXT.name()}; padding: 7px 10px; border-radius: 6px;
            }}
            QTimeEdit:focus, QLineEdit:focus {{ border-color: {C_ACCENT.name()}; }}
        """)

        lay = QVBoxLayout(self)
        lay.setSpacing(14); lay.setContentsMargins(22, 20, 22, 20)

        title = QLabel("Edit Activity" if is_edit else "Log Activity")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        lay.addWidget(title)

        # Type buttons grid
        grid_w = QWidget()
        grid   = QGridLayout(grid_w)
        grid.setSpacing(5); grid.setContentsMargins(0,0,0,0)
        self._type_btns = {}
        for i, at in enumerate(ACTIVITY_TYPES):
            btn = QPushButton(f"{at['icon']} {at['label']}")
            btn.setCheckable(True)
            btn.setChecked(at["id"] == sel_type)
            self._apply_type_style(btn, at, at["id"] == sel_type)
            btn.clicked.connect(lambda _, aid=at["id"]: self._pick(aid))
            self._type_btns[at["id"]] = (btn, at)
            grid.addWidget(btn, i // 3, i % 3)
        lay.addWidget(grid_w)

        # Times — respect the exact range the user dragged/clicked
        trow = QHBoxLayout()
        end_min = max(end_min, start_min + 15)
        self.t_start = QTimeEdit(QTime(start_min // 60, start_min % 60))
        self.t_end   = QTimeEdit(QTime((end_min // 60) % 24, end_min % 60))
        for lbl, w in [("Start", self.t_start), ("End", self.t_end)]:
            col = QVBoxLayout()
            ql  = QLabel(lbl.upper())
            ql.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 10px;")
            col.addWidget(ql); col.addWidget(w)
            trow.addLayout(col)
        lay.addLayout(trow)

        # Optional title
        ql2 = QLabel("TITLE (optional)")
        ql2.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 10px;")
        lay.addWidget(ql2)
        self.txt = QLineEdit(placeholderText="What are you up to?")
        if is_edit:
            self.txt.setText(existing.get("title", ""))
        lay.addWidget(self.txt)

        # Buttons
        brow = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {C_BORDER.name()};
            color: {C_MUTED.name()}; padding: 8px 16px; border-radius: 7px; }}
            QPushButton:hover {{ color: {C_TEXT.name()}; border-color: {C_BORDER2.name()}; }}
        """)
        cancel.clicked.connect(self.reject)
        if is_edit:
            delete = QPushButton("Delete")
            delete.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: 1px solid rgba(239,68,68,.4);
                color: #fca5a5; padding: 8px 16px; border-radius: 7px; }}
                QPushButton:hover {{ background: rgba(239,68,68,.15); border-color: #ef4444; }}
            """)
            delete.clicked.connect(self._delete)
            brow.addWidget(delete)
        brow.addStretch()
        save = QPushButton("Save Changes" if is_edit else "Add to Schedule")
        save.setStyleSheet(f"""
            QPushButton {{ background: {C_ACCENT.name()}; color: white; padding: 8px 16px;
            border-radius: 7px; font-weight: bold; border: none; }}
            QPushButton:hover {{ background: {C_ACCENT2.name()}; }}
        """)
        save.clicked.connect(self._save)
        brow.addWidget(cancel); brow.addWidget(save)
        lay.addLayout(brow)

    def _apply_type_style(self, btn, at, selected):
        c = at["color"]
        if selected:
            btn.setStyleSheet(f"""
                QPushButton {{ background: {c}30; border: 1.5px solid {c}; color: white;
                font-weight: bold; padding: 5px 4px; border-radius: 6px; font-size: 11px; }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_MUTED.name()}; padding: 5px 4px; border-radius: 6px; font-size: 11px; }}
                QPushButton:hover {{ border-color: {C_BORDER2.name()}; color: {C_TEXT.name()}; }}
            """)

    def _pick(self, type_id):
        self._sel = type_id
        for tid, (btn, at) in self._type_btns.items():
            btn.setChecked(tid == type_id)
            self._apply_type_style(btn, at, tid == type_id)

    def _save(self):
        st = self.t_start.time(); en = self.t_end.time()
        sm = st.hour() * 60 + st.minute()
        em = en.hour() * 60 + en.minute()
        if em <= sm:
            QMessageBox.warning(self, "Invalid", "End must be after start."); return
        at = next((t for t in ACTIVITY_TYPES if t["id"] == self._sel), ACTIVITY_TYPES[0])
        self.result_activity = {
            "id": self._existing["id"] if self._existing else new_id(),
            "date": self._for_date,
            "startMin": sm, "endMin": em,
            "type": at["id"], "color": at["color"],
            "title": self.txt.text().strip() or f"{at['icon']} {at['label']}",
        }
        self.accept()

    def _delete(self):
        self.result_deleted = True
        self.accept()

# ══════════════════════════════════════════════════════════════════════════
#  SIDEBAR  (activity type picker + daily summary)
# ══════════════════════════════════════════════════════════════════════════
class SidebarWidget(QWidget):
    type_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(210)
        self.setStyleSheet(f"""
            QWidget {{ background: {C_SURFACE.name()}; }}
            QLabel  {{ background: transparent; color: {C_TEXT.name()}; }}
        """)
        self._sel = "study"
        self._type_btns: Dict[str, tuple] = {}

        lay = QVBoxLayout(self)
        lay.setSpacing(0); lay.setContentsMargins(0,0,0,0)

        # ── Add activity section ───────────────────────────────────────────
        add_sec = QWidget()
        add_sec.setStyleSheet(f"border-bottom: 1px solid {C_BORDER.name()};")
        al = QVBoxLayout(add_sec)
        al.setContentsMargins(12, 14, 12, 14); al.setSpacing(8)

        hl = QLabel("ADD ACTIVITY")
        hl.setStyleSheet(f"font-size: 9px; font-weight: bold; letter-spacing: 1px; color: {C_MUTED.name()};")
        al.addWidget(hl)

        grid = QGridLayout(); grid.setSpacing(5); grid.setContentsMargins(0,0,0,0)
        for i, at in enumerate(ACTIVITY_TYPES):
            btn = QPushButton(f"{at['icon']} {at['label']}")
            btn.setCheckable(True)
            btn.setChecked(at["id"] == "study")
            self._set_chip_style(btn, at, at["id"] == "study")
            btn.clicked.connect(lambda _, aid=at["id"]: self._select(aid))
            self._type_btns[at["id"]] = (btn, at)
            grid.addWidget(btn, i // 2, i % 2)
        al.addLayout(grid)

        hint = QLabel("Pick a type, then drag on the\ntimeline to create a block\n(or click for a quick 1-hour block).")
        hint.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 10px;")
        al.addWidget(hint)
        lay.addWidget(add_sec)

        # ── Summary section ────────────────────────────────────────────────
        sum_sec = QWidget()
        sl = QVBoxLayout(sum_sec)
        sl.setContentsMargins(12, 14, 12, 8); sl.setSpacing(6)

        sh = QLabel("TODAY'S SUMMARY")
        sh.setStyleSheet(f"font-size: 9px; font-weight: bold; letter-spacing: 1px; color: {C_MUTED.name()};")
        sl.addWidget(sh)

        self._sum_area = QVBoxLayout(); self._sum_area.setSpacing(6)
        sl.addLayout(self._sum_area)
        lay.addWidget(sum_sec)
        lay.addStretch()

    def _set_chip_style(self, btn, at, selected):
        c = at["color"]
        if selected:
            btn.setStyleSheet(f"""
                QPushButton {{ background: {c}28; border: 1.5px solid {c}; color: white;
                font-weight: bold; padding: 4px 5px; border-radius: 6px; font-size: 10px; }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_MUTED.name()}; padding: 4px 5px; border-radius: 6px; font-size: 10px; }}
                QPushButton:hover {{ border-color: {C_BORDER2.name()}; color: {C_TEXT.name()}; }}
            """)

    def _select(self, tid):
        self._sel = tid
        for aid, (btn, at) in self._type_btns.items():
            btn.setChecked(aid == tid)
            self._set_chip_style(btn, at, aid == tid)
        self.type_selected.emit(tid)

    @property
    def selected_type(self): return self._sel

    def update_summary(self, cal_events, activities):
        while self._sum_area.count():
            item = self._sum_area.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        all_b = cal_events + activities
        DAY_T = DAY_END - DAY_START
        totals: Dict[str, int] = {}
        for b in all_b:
            totals[b["type"]] = totals.get(b["type"], 0) + (b["endMin"] - b["startMin"])

        cats = [
            {"id": "calendar", "label": "Meetings", "color": "#3b82f6"},
        ] + [{"id": t["id"], "label": t["label"], "color": t["color"]} for t in ACTIVITY_TYPES]

        for cat in cats:
            mins = totals.get(cat["id"], 0)
            if not mins: continue
            row = QWidget()
            rl  = QVBoxLayout(row)
            rl.setContentsMargins(0,0,0,0); rl.setSpacing(3)

            top = QHBoxLayout(); top.setSpacing(6)
            dot = QLabel("●"); dot.setStyleSheet(f"color: {cat['color']}; font-size: 9px;")
            lbl = QLabel(cat["label"]); lbl.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 11px;")
            val = QLabel(fmt_dur(mins)); val.setStyleSheet(f"color: {C_TEXT.name()}; font-size: 11px; font-weight: bold;")
            top.addWidget(dot); top.addWidget(lbl, 1); top.addWidget(val)
            rl.addLayout(top)

            bar = QProgressBar()
            bar.setFixedHeight(3)
            bar.setTextVisible(False)
            bar.setRange(0, DAY_T)
            bar.setValue(mins)
            bar.setStyleSheet(f"""
                QProgressBar {{ background: {C_BORDER.name()}; border-radius: 2px; border: none; }}
                QProgressBar::chunk {{ background: {cat['color']}; border-radius: 2px; }}
            """)
            rl.addWidget(bar)
            self._sum_area.addWidget(row)

# ══════════════════════════════════════════════════════════════════════════
#  MONTH VIEW  (Google-Calendar-style month grid)
# ══════════════════════════════════════════════════════════════════════════
class MonthViewWidget(QWidget):
    day_clicked = Signal(object)   # datetime.date

    HDR_H = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self._year  = date.today().year
        self._month = date.today().month
        self._events: Dict[str, List[Dict]] = {}
        self._hits: List[tuple] = []
        self.setMinimumHeight(480)
        self.setCursor(Qt.PointingHandCursor)

    def set_month(self, year, month, events_by_date):
        self._year, self._month = year, month
        self._events = events_by_date
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), C_BG)
        self._hits = []

        weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(self._year, self._month)
        cw = self.width() / 7.0
        ch = (self.height() - self.HDR_H) / len(weeks)
        today = date.today()

        p.setFont(QFont("Segoe UI", 8, QFont.Bold))
        p.setPen(C_MUTED)
        for i, nm in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]):
            p.drawText(QRect(int(i * cw), 0, int(cw), self.HDR_H), Qt.AlignCenter, nm)

        fn_day  = QFont("Segoe UI", 9)
        fn_chip = QFont("Segoe UI", 8)
        fm_chip = QFontMetrics(fn_chip)

        for r, week in enumerate(weeks):
            for c, d in enumerate(week):
                x = int(c * cw); y = int(self.HDR_H + r * ch)
                cell = QRect(x, y, int(cw), int(ch))
                self._hits.append((cell, d))

                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(C_BORDER, 1))
                p.drawRect(cell)

                in_month = (d.month == self._month)
                if d == today:
                    p.setBrush(C_ACCENT); p.setPen(Qt.NoPen)
                    p.drawEllipse(QRect(x + 5, y + 3, 20, 20))
                    p.setPen(QColor("white"))
                else:
                    p.setPen(C_TEXT if in_month else QColor("#3c3c52"))
                p.setFont(fn_day)
                p.drawText(QRect(x + 5, y + 3, 20, 20), Qt.AlignCenter, str(d.day))

                evs = sorted(self._events.get(d.isoformat(), []),
                             key=lambda b: b.get("startMin", 0))
                if not evs:
                    continue
                max_chips = max(0, int((ch - 30) // 17))
                shown = evs[:max_chips]
                p.setFont(fn_chip)
                for i, ev in enumerate(shown):
                    cy   = y + 27 + i * 17
                    chip = QRect(x + 4, int(cy), int(cw) - 8, 14)
                    col  = QColor(ev.get("color", "#7c6ff7"))
                    if not in_month:
                        col.setAlpha(120)
                    bg = QColor(col); bg.setAlpha(45)
                    p.setPen(Qt.NoPen); p.setBrush(bg)
                    p.drawRoundedRect(chip, 4, 4)
                    p.setPen(col)
                    label = f"{fmt_time(ev.get('startMin', 0))} {ev.get('title', '')}"
                    p.drawText(chip.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm_chip.elidedText(label, Qt.ElideRight, chip.width() - 8))
                if len(evs) > len(shown):
                    p.setPen(C_MUTED)
                    p.drawText(QRect(x + 8, int(y + 27 + len(shown) * 17), int(cw) - 12, 13),
                               Qt.AlignVCenter | Qt.AlignLeft, f"+{len(evs) - len(shown)} more")

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        for rect, d in self._hits:
            if rect.contains(pos):
                self.day_clicked.emit(d)
                return

# ══════════════════════════════════════════════════════════════════════════
#  YEAR VIEW  (12 mini-months, busy days dotted)
# ══════════════════════════════════════════════════════════════════════════
class YearViewWidget(QWidget):
    day_clicked = Signal(object)   # datetime.date

    def __init__(self, parent=None):
        super().__init__(parent)
        self._year = date.today().year
        self._busy: set = set()
        self._hits: List[tuple] = []
        self.setMinimumSize(860, 660)
        self.setCursor(Qt.PointingHandCursor)

    def set_year(self, year, busy_dates):
        self._year = year
        self._busy = {b for b in busy_dates if b}
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), C_BG)
        self._hits = []
        today = date.today()

        cols, rows = 4, 3
        mw = self.width()  / cols
        mh = self.height() / rows
        fn_title = QFont("Segoe UI", 10, QFont.Bold)
        fn_hdr   = QFont("Segoe UI", 7)
        fn_day   = QFont("Segoe UI", 8)

        for m in range(1, 13):
            ox = ((m - 1) % cols) * mw + 14
            oy = ((m - 1) // cols) * mh + 10

            p.setFont(fn_title); p.setPen(C_ACCENT)
            p.drawText(QRect(int(ox), int(oy), int(mw) - 28, 18),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       date(self._year, m, 1).strftime("%B"))

            cw  = (mw - 28) / 7.0
            chh = (mh - 50) / 7.0
            rad = max(3, int(min(cw, chh) / 2) - 1)

            p.setFont(fn_hdr); p.setPen(C_MUTED)
            for i, ltr in enumerate("SMTWTFS"):
                p.drawText(QRect(int(ox + i * cw), int(oy + 20), int(cw), int(chh)),
                           Qt.AlignCenter, ltr)

            weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(self._year, m)
            for r, week in enumerate(weeks):
                for c, d in enumerate(week):
                    if d.month != m:
                        continue
                    cell = QRect(int(ox + c * cw), int(oy + 20 + (r + 1) * chh),
                                 int(cw), int(chh))
                    self._hits.append((cell, d))
                    p.setFont(fn_day)
                    if d == today:
                        p.setBrush(C_ACCENT); p.setPen(Qt.NoPen)
                        p.drawEllipse(cell.center(), rad, rad)
                        p.setPen(QColor("white"))
                    elif d.isoformat() in self._busy:
                        bg = QColor(C_ACCENT); bg.setAlpha(55)
                        p.setBrush(bg); p.setPen(Qt.NoPen)
                        p.drawEllipse(cell.center(), rad, rad)
                        p.setPen(C_TEXT)
                    else:
                        p.setPen(C_MUTED)
                    p.setBrush(Qt.NoBrush)
                    p.drawText(cell, Qt.AlignCenter, str(d.day))

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        for rect, d in self._hits:
            if rect.contains(pos):
                self.day_clicked.emit(d)
                return

# ══════════════════════════════════════════════════════════════════════════
#  AI ASSISTANT PANEL
# ══════════════════════════════════════════════════════════════════════════
class AIPanel(QWidget):
    def __init__(self, get_ctx_fn, parent=None):
        super().__init__(parent)
        self.get_ctx    = get_ctx_fn
        self.model      = DEFAULT_MODEL
        self.mode       = "chat"
        self.history: Dict[str, List[Dict]] = {
            "chat": [{"role": "assistant", "content": AI_GREETING}],
            "plan": [], "suggest": []}
        self._thread: Optional[OllamaThread] = None
        self._cur_text  = ""
        self._ollama_up = False
        self.execute_tool = None          # set by MainWindow: fn(name, args) -> str
        self._loop_msgs: List[Dict] = []  # running conversation for the tool loop
        self._depth = 0                   # tool-round counter (loop guard)

        self.setFixedWidth(320)
        self.setStyleSheet(f"background: {C_SURFACE.name()}; color: {C_TEXT.name()};")

        lay = QVBoxLayout(self); lay.setSpacing(0); lay.setContentsMargins(0,0,0,0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet(f"border-bottom: 1px solid {C_BORDER.name()};")
        hl  = QVBoxLayout(hdr); hl.setContentsMargins(12,10,12,8); hl.setSpacing(6)

        tr = QHBoxLayout()
        t  = QLabel("🤖 AI Assistant"); t.setStyleSheet("font-size: 13px; font-weight: bold;")
        tr.addWidget(t)
        self._dot = QLabel("●"); self._dot.setStyleSheet(f"color: {C_MUTED.name()};")
        self._stxt = QLabel("Checking…"); self._stxt.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 11px;")
        tr.addWidget(self._dot); tr.addWidget(self._stxt); tr.addStretch()

        self._unload_btn = QPushButton("⏏")
        self._unload_btn.setToolTip("Unload model from memory (keeps Ollama running)")
        self._unload_btn.setFixedSize(26, 24)
        self._unload_btn.setCursor(Qt.PointingHandCursor)
        self._unload_btn.setEnabled(False)
        self._unload_btn.setStyleSheet(f"""
            QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_MUTED.name()}; border-radius: 5px; font-size: 12px; }}
            QPushButton:hover {{ background: rgba(124,111,247,.18); border-color: {C_ACCENT.name()}; color: {C_ACCENT.name()}; }}
            QPushButton:disabled {{ color: {C_BORDER2.name()}; border-color: {C_BORDER.name()}; }}
        """)
        self._unload_btn.clicked.connect(self._unload_model)
        tr.addWidget(self._unload_btn)

        self._power_btn = QPushButton("▶")
        self._power_btn.setFixedSize(26, 24)
        self._power_btn.setCursor(Qt.PointingHandCursor)
        self._power_btn.clicked.connect(self._toggle_power)
        tr.addWidget(self._power_btn)
        self._set_power_state(False)
        hl.addLayout(tr)

        mr = QHBoxLayout()
        mr.addWidget(QLabel("Model:", styleSheet=f"color:{C_MUTED.name()}; font-size:10px;"))
        self._model_in = QLineEdit(self.model)
        self._model_in.setFixedHeight(24)
        self._model_in.setStyleSheet(f"""
            QLineEdit {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_TEXT.name()}; padding: 3px 8px; border-radius: 5px; font-size: 11px; }}
        """)
        self._model_in.editingFinished.connect(
            lambda: setattr(self, "model", self._model_in.text().strip() or DEFAULT_MODEL))
        mr.addWidget(self._model_in)
        hl.addLayout(mr)
        lay.addWidget(hdr)

        # Tabs
        tabs = QWidget(); tabs.setStyleSheet(f"border-bottom: 1px solid {C_BORDER.name()};")
        tl   = QHBoxLayout(tabs); tl.setContentsMargins(0,0,0,0); tl.setSpacing(0)
        self._tabs = {}
        for mode, lbl in [("chat","💬 Chat"), ("plan","📋 Plan"), ("suggest","✨ Analyze")]:
            b = QPushButton(lbl); b.setCheckable(True); b.setChecked(mode=="chat")
            b.setStyleSheet(self._tab_style(mode == "chat"))
            b.clicked.connect(lambda _, m=mode: self._set_mode(m))
            self._tabs[mode] = b; tl.addWidget(b)
        lay.addWidget(tabs)

        # Messages
        self._msgs_view = QTextEdit()
        self._msgs_view.setReadOnly(True)
        self._msgs_view.setStyleSheet(f"""
            QTextEdit {{ background: {C_BG.name()}; border: none;
            color: {C_TEXT.name()}; font-size: 12px; padding: 8px; }}
        """)
        lay.addWidget(self._msgs_view, 1)

        self._thinking = QLabel("⟳  Thinking…")
        self._thinking.setStyleSheet(f"color:{C_MUTED.name()}; font-size:11px; padding:4px 12px;")
        self._thinking.hide()
        lay.addWidget(self._thinking)

        # Input
        inp = QWidget(); il = QVBoxLayout(inp); il.setContentsMargins(8,6,8,8); il.setSpacing(4)
        self._inp = QTextEdit()
        self._inp.setMaximumHeight(72)
        self._inp.setPlaceholderText("Ask me anything about your day…")
        self._inp.setStyleSheet(f"""
            QTextEdit {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_TEXT.name()}; padding: 6px; border-radius: 6px; font-size: 12px; }}
            QTextEdit:focus {{ border-color: {C_ACCENT.name()}; }}
        """)
        il.addWidget(self._inp)

        br = QHBoxLayout()
        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setStyleSheet("background:rgba(239,68,68,.2); color:#fca5a5; border-radius:5px; padding:3px 10px;")
        self._stop_btn.hide()
        self._stop_btn.clicked.connect(self._stop)

        send = QPushButton("Send ↑")
        send.setStyleSheet(f"""
            QPushButton {{ background:{C_ACCENT.name()}; color:white; border-radius:6px;
            font-weight:bold; padding:5px 14px; border:none; }}
            QPushButton:hover {{ background:{C_ACCENT2.name()}; }}
        """)
        send.clicked.connect(self._send)
        br.addWidget(self._stop_btn); br.addStretch(); br.addWidget(send)
        il.addLayout(br)
        lay.addWidget(inp)

        self._render()
        self._poll_ollama()
        self._timer = QTimer(self); self._timer.timeout.connect(self._poll_ollama); self._timer.start(30_000)

    def _tab_style(self, active):
        return (f"QPushButton {{ background:transparent; border:none; border-bottom:2px solid {C_ACCENT.name()};"
                f"color:{C_ACCENT.name()}; padding:8px 4px; font-size:12px; }}" if active else
                f"QPushButton {{ background:transparent; border:none; border-bottom:2px solid transparent;"
                f"color:{C_MUTED.name()}; padding:8px 4px; font-size:12px; }}"
                f"QPushButton:hover {{ color:{C_TEXT.name()}; }}")

    def _poll_ollama(self):
        t = OllamaCheckThread(self)
        t.result.connect(self._on_ollama)
        t.start()

    def _on_ollama(self, ok: bool):
        self._ollama_up = ok
        self._dot.setStyleSheet(f"color: {'#10b981' if ok else '#ef4444'};")
        if not self._stxt.text().startswith("Starting"):
            self._stxt.setText("Connected" if ok else "Not running")
        self._set_power_state(ok)
        self._unload_btn.setEnabled(ok)

    def _set_power_state(self, up: bool):
        """Power button is a toggle: ▶ Start when down, ⏻ Stop when up."""
        if up:
            self._power_btn.setText("⏻")
            self._power_btn.setToolTip("Stop Ollama (shuts down the local LLM server)")
            self._power_btn.setStyleSheet(f"""
                QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_MUTED.name()}; border-radius: 5px; font-size: 13px; }}
                QPushButton:hover {{ background: rgba(239,68,68,.18); border-color: #ef4444; color: #fca5a5; }}
            """)
        else:
            self._power_btn.setText("▶")
            self._power_btn.setToolTip("Start Ollama (launches the local LLM server)")
            self._power_btn.setStyleSheet(f"""
                QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
                color: {C_MUTED.name()}; border-radius: 5px; font-size: 12px; }}
                QPushButton:hover {{ background: rgba(16,185,129,.18); border-color: #10b981; color: #6ee7b7; }}
            """)

    def _toggle_power(self):
        if self._ollama_up:
            self._shutdown_ollama()
        else:
            self._start_ollama()

    def _start_ollama(self):
        ok, msg = start_ollama()
        if not ok:
            QMessageBox.information(self, "Ollama", msg)
            return
        self._stxt.setText("Starting…")
        self._dot.setStyleSheet("color: #f59e0b;")  # amber while booting
        # Server takes a moment to bind the port — poll a few times as it comes up
        for delay in (700, 1500, 2500, 4000, 6000, 9000):
            QTimer.singleShot(delay, self._poll_ollama)

    def _unload_model(self):
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        ok, msg = unload_ollama_model(self.model)
        self._poll_ollama()  # server stays up, so dot should remain green
        QMessageBox.information(self, "Ollama", msg)

    def _shutdown_ollama(self):
        confirm = QMessageBox.question(
            self, "Stop Ollama",
            "Stop the local Ollama server?\n\n"
            "The AI assistant won't respond until you start it again "
            "(the ▶ button, the Ollama app, or 'ollama serve').",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        ok, msg = stop_ollama()
        self._poll_ollama()  # refresh status dot immediately
        if not ok:
            QMessageBox.information(self, "Ollama", msg)

    def _set_mode(self, mode):
        self.mode = mode
        for m, b in self._tabs.items():
            b.setChecked(m == mode)
            b.setStyleSheet(self._tab_style(m == mode))
        hints = {"plan": "Describe what you need to accomplish today…",
                 "suggest": "Ask for specific suggestions…"}
        self._inp.setPlaceholderText(hints.get(mode, "Ask me anything about your day…"))
        self._render()
        if mode == "suggest" and not self.history["suggest"]:
            QTimer.singleShot(200, lambda: self._generate(None))

    def _render(self):
        msgs = self.history[self.mode]
        if not msgs:
            hints = {
                "plan":    "Tell me what you need to accomplish today and I'll build a schedule.",
                "suggest": "Analyzing your schedule…",
            }
            h = hints.get(self.mode, "Ask about your day, tasks, or how to be more productive.")
            self._msgs_view.setHtml(
                f'<p style="color:{C_MUTED.name()}; font-style:italic; text-align:center; margin-top:20px;">{h}</p>')
            return

        html = ""
        for msg in msgs:
            c = msg["content"].replace("&","&amp;").replace("<","&lt;").replace("\n","<br>")
            r = msg["role"]
            if r == "user":
                html += (f'<div style="text-align:right;margin:4px 0;">'
                         f'<span style="background:{C_ACCENT.name()};color:white;padding:6px 10px;'
                         f'border-radius:10px 10px 2px 10px;display:inline-block;max-width:88%;font-size:12px;">'
                         f'{c}</span></div>')
            elif r == "assistant":
                html += (f'<div style="margin:4px 0;">'
                         f'<span style="background:{C_SURF2.name()};border:1px solid {C_BORDER.name()};'
                         f'color:{C_TEXT.name()};padding:8px 10px;border-radius:2px 10px 10px 10px;'
                         f'display:inline-block;font-size:12px;white-space:pre-wrap;">{c}</span></div>')
            elif r == "tool_note":
                html += (f'<div style="margin:4px 0;background:rgba(16,185,129,.08);'
                         f'border:1px solid rgba(16,185,129,.25);color:#6ee7b7;padding:6px 8px;'
                         f'border-radius:6px;font-size:11px;">{c}</div>')
            elif r == "error":
                html += (f'<div style="margin:4px 0;background:rgba(239,68,68,.1);'
                         f'border:1px solid rgba(239,68,68,.3);color:#fca5a5;padding:8px;'
                         f'border-radius:6px;font-size:12px;">{c}</div>')
        self._msgs_view.setHtml(html)
        self._msgs_view.verticalScrollBar().setValue(self._msgs_view.verticalScrollBar().maximum())

    def _sys_prompt(self):
        ctx = self.get_ctx()
        types_line = " · ".join(f"{t['id']} ({t['label']})" for t in ACTIVITY_TYPES)
        p = (
            "You are the scheduling assistant built into Daily Scheduler, a desktop "
            "day-planner. You help the user (a high-school student) plan study, projects, "
            "exercise, downtime, and social time, and you edit their calendar directly "
            "with tools.\n\n"
            "THE DAY\n"
            "\"Today\" is the day currently shown on screen. Anything asked for without a "
            "date goes on that day. For another day the user names it (e.g. \"6/14\", "
            "\"tomorrow\") — pass that straight into the tool's date argument; the app "
            "resolves the exact date. Omit the date for the day on screen.\n\n"
            "ACTIVITY TYPES (use the id in a block's \"type\" field; pick the best fit):\n"
            f"  {types_line}\n\n"
            "SCHEDULE (day on screen)\n"
            "Google Calendar (READ-ONLY — you cannot change these):\n")
        cal = ctx.get("cal_events", [])
        p += "".join(f"  - {e['title']}: {fmt_time(e['startMin'])}–{fmt_time(e['endMin'])}\n"
                     for e in cal) or "  (none)\n"
        p += "Your editable blocks:\n"
        acts = ctx.get("activities", [])
        p += "".join(f"  - \"{a['title']}\" [{a['type']}]: "
                     f"{fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}\n"
                     for a in acts) or "  (none yet)\n"
        p += (
            "\nTOOLS — pick the ONE that fits; never chain small calls for a bulk job:\n"
            "  add_block     – add one block\n"
            "  move_block    – change a block's time, length, day, or title (match by title)\n"
            "  delete_block  – remove a block by title\n"
            "  clear_day     – wipe a whole day\n"
            "  shift_blocks  – move EVERY block on a day by an offset (\"push everything 2h later\")\n"
            "  copy_day      – duplicate all blocks from one day to another (\"copy today to 6/14\")\n"
            "  replace_day   – rebuild a whole day from a complete list (restructuring, splitting\n"
            "                  into chunks, planning with breaks). It DELETES blocks you don't\n"
            "                  include, so list everything you want to keep.\n"
            "  list_blocks   – read a day's schedule\n\n"
            "RULES\n"
            "  - ALWAYS call a tool when asked to add/move/remove/rename/copy/clear/shift/plan "
            "— never just describe the change.\n"
            "  - Times are 24-hour HH:MM. For another day pass the date the user gave "
            "(e.g. \"6/14\", \"tomorrow\"); omit it for the day on screen.\n"
            "  - Blocks can't overlap — the app auto-adjusts, so don't fuss over exact gaps.\n"
            "  - To move/delete/rename, match a block by its title (partial titles work).\n"
            "  - Google Calendar events are READ-ONLY; if asked to change one, say it must "
            "be edited in Google Calendar.\n"
            "  - CHECK YOUR WORK: after any edit — especially several at once or a whole-day "
            "rebuild — call list_blocks to confirm the day came out right: the correct "
            "blocks exist, times/durations match what was asked, nothing overlaps, and "
            "nothing was deleted by accident. If anything is wrong, fix it and check again. "
            "Repeat until the schedule is correct. Only tell the user it's done once you have "
            "verified it.\n"
            "  - After it's verified, confirm in one short sentence — don't restate the whole "
            "schedule.\n"
            "  - Be friendly and concise.\n\n"
            "EXAMPLES\n"
            "  \"copy my schedule to 6/14\"         → copy_day(to_date=\"6/14\")\n"
            "  \"shift everything two hours later\"  → shift_blocks(minutes=120)\n"
            "  \"move AP work to 1pm\"              → move_block(title=\"AP work\", start=\"13:00\")\n"
            "  \"make gym 30 minutes longer\"       → move_block(title=\"gym\", end=\"...\")\n"
            "  \"clear tomorrow\"                   → clear_day(date=\"tomorrow\")\n"
            "  \"split my afternoon into 45-min study blocks with breaks\" → replace_day(blocks=[...])\n")
        add = {"plan": "\nThe user wants help planning. Propose a plan with explicit time "
                       "ranges, then create the agreed blocks (replace_day for a full day).",
               "suggest": "\nGive 3-5 specific, actionable schedule improvements."}.get(self.mode, "")
        return p + add

    def _send(self):
        txt = self._inp.toPlainText().strip()
        if not txt: return
        self._inp.clear()
        self.history[self.mode].append({"role": "user", "content": txt})
        self._render(); self._generate(txt)

    def _generate(self, user_msg):
        if self._thread and self._thread.isRunning(): return
        hist = [m for m in self.history[self.mode] if m["role"] in ("user","assistant")]
        msgs = [{"role":"system","content":self._sys_prompt()}] + \
               [{"role":m["role"],"content":m["content"]} for m in hist if m["content"]]

        self.history[self.mode].append({"role":"assistant","content":""})
        self._cur_text  = ""
        self._loop_msgs = msgs
        self._depth     = 0
        self._thinking.show(); self._stop_btn.show()
        self._spawn_thread()

    def _spawn_thread(self):
        self._thread = OllamaThread(self._loop_msgs, self.model, tools=AI_TOOLS)
        self._thread.token.connect(self._on_token)
        self._thread.done.connect(self._on_done)
        self._thread.tool_calls.connect(self._on_tool_calls)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_tool_calls(self, calls):
        h = self.history[self.mode]
        # drop the empty streaming bubble; tool notes take its place
        if h and h[-1]["role"] == "assistant" and not h[-1]["content"].strip():
            h.pop()
        self._loop_msgs = self._loop_msgs + [
            {"role": "assistant", "content": self._cur_text or "", "tool_calls": calls}]
        for call in calls:
            fn   = call.get("function") or {}
            name = fn.get("name", "?")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception: args = {}
            result = self.execute_tool(name, args) if callable(self.execute_tool) \
                     else "Tool execution unavailable."
            h.append({"role": "tool_note", "content": f"🛠 {name} → {result}"})
            self._loop_msgs.append({"role": "tool", "tool_name": name,
                                    "name": name, "content": str(result)})
        self._render()
        self._depth += 1
        if self._depth >= MAX_TOOL_ROUNDS:   # guard against tool-call loops
            self._thinking.hide(); self._stop_btn.hide()
            return
        h.append({"role": "assistant", "content": ""})
        self._cur_text = ""
        self._spawn_thread()

    def _on_token(self, tok):
        self._cur_text += tok
        if looks_like_tool_text(self._cur_text):
            # Model is printing a tool call as text — don't show raw JSON; keep the
            # "Thinking…" indicator up. _on_done will execute it.
            self.history[self.mode][-1]["content"] = ""
            self._render()
        else:
            self.history[self.mode][-1]["content"] = self._cur_text
            self._render(); self._thinking.hide()

    def _on_done(self):
        # Small models sometimes print the tool call as text (<|python_tag|>, ``` fences,
        # bare JSON, arrays…) instead of using the native tool_calls channel. Recover it.
        extracted = extract_tool_calls(self._cur_text) if self._depth < MAX_TOOL_ROUNDS else []
        if extracted:
            h = self.history[self.mode]
            if h and h[-1]["role"] == "assistant":
                h.pop()   # drop the (hidden) raw-text bubble
            self._cur_text = ""
            self._on_tool_calls([{"function": {"name": e["name"], "arguments": e["args"]}}
                                 for e in extracted])
            return
        # Not a tool call. Restore the real text (it may have been hidden mid-stream
        # because it looked tool-like), or show a fallback if it was unparseable JSON.
        h = self.history[self.mode]
        if h and h[-1]["role"] == "assistant":
            if self._cur_text and not looks_like_tool_text(self._cur_text):
                h[-1]["content"] = self._cur_text
            elif looks_like_tool_text(self._cur_text):
                h[-1]["content"] = ("I tried to update your schedule but couldn't read "
                                    "the result — could you rephrase that?")
            self._render()
        self._thinking.hide(); self._stop_btn.hide()

    def _on_error(self, msg):
        self.history[self.mode].pop()
        self.history[self.mode].append({"role":"error","content":msg})
        self._render(); self._thinking.hide(); self._stop_btn.hide()

    def _stop(self):
        if self._thread: self._thread.stop()

# ══════════════════════════════════════════════════════════════════════════
#  SETUP SCREEN
# ══════════════════════════════════════════════════════════════════════════
class SetupWidget(QWidget):
    proceed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {C_BG.name()};")
        outer = QVBoxLayout(self); outer.setAlignment(Qt.AlignCenter)

        card = QWidget(); card.setFixedWidth(520)
        card.setStyleSheet(f"""
            QWidget {{ background: {C_SURFACE.name()}; border-radius: 14px; color: {C_TEXT.name()}; }}
            QLabel  {{ background: transparent; }}
        """)
        cl = QVBoxLayout(card); cl.setSpacing(14); cl.setContentsMargins(40,36,40,36)

        title = QLabel("📅 Daily Scheduler")
        title.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {C_ACCENT.name()};")
        cl.addWidget(title)

        sub = QLabel("A native desktop app for planning your day.\n"
                     "Optionally connect Google Calendar or just use it offline.")
        sub.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 13px;"); sub.setWordWrap(True)
        cl.addWidget(sub)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {C_BORDER.name()};"); cl.addWidget(sep)

        gcal = QLabel("Google Calendar (optional)")
        gcal.setStyleSheet("font-size: 13px; font-weight: bold;"); cl.addWidget(gcal)

        steps = QLabel(
            "1.  console.cloud.google.com → create project → enable Calendar API\n"
            "2.  APIs & Services → Credentials → + Create Credentials\n"
            "     → OAuth 2.0 Client ID → Desktop application → Download JSON\n"
            "3.  Load that file below — the app stores it in ~/.daily-scheduler/"
        )
        steps.setStyleSheet(f"color: {C_MUTED.name()}; font-size: 12px;"); steps.setWordWrap(True)
        cl.addWidget(steps)

        have = CREDS_FILE.exists()
        self._creds_lbl = QLabel("✓ credentials.json loaded" if have else "No credentials loaded")
        self._creds_lbl.setStyleSheet(f"color: {'#10b981' if have else C_MUTED.name()}; font-size: 12px;")
        cl.addWidget(self._creds_lbl)

        load_btn = QPushButton("Load credentials.json…")
        load_btn.setStyleSheet(f"""
            QPushButton {{ background: {C_SURF2.name()}; border: 1px solid {C_BORDER.name()};
            color: {C_TEXT.name()}; padding: 7px 14px; border-radius: 7px; font-size: 12px; border-style:solid; }}
            QPushButton:hover {{ border-color: {C_BORDER2.name()}; }}
        """)
        load_btn.clicked.connect(self._load)
        cl.addWidget(load_btn)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color: {C_BORDER.name()};"); cl.addWidget(sep2)

        ar = QHBoxLayout()
        go = QPushButton("Connect Google & Open")
        go.setStyleSheet(f"""
            QPushButton {{ background:{C_ACCENT.name()}; color:white; padding:9px 18px;
            border-radius:7px; font-weight:bold; border:none; font-size:13px; }}
            QPushButton:hover {{ background:{C_ACCENT2.name()}; }}
        """)
        go.clicked.connect(self._connect)

        skip = QPushButton("Use Without Google")
        skip.setStyleSheet(f"""
            QPushButton {{ background:transparent; border:1px solid {C_BORDER.name()};
            color:{C_MUTED.name()}; padding:9px 18px; border-radius:7px; font-size:13px; }}
            QPushButton:hover {{ color:{C_TEXT.name()}; border-color:{C_BORDER2.name()}; }}
        """)
        skip.clicked.connect(self.proceed.emit)
        ar.addWidget(go); ar.addWidget(skip)
        cl.addLayout(ar)

        self._warn = QLabel(""); self._warn.setStyleSheet("color: #fca5a5; font-size: 12px;")
        self._warn.setWordWrap(True); self._warn.hide(); cl.addWidget(self._warn)

        outer.addWidget(card)

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load credentials.json", "", "JSON (*.json)")
        if path:
            shutil.copy(path, str(CREDS_FILE))
            self._creds_lbl.setText("✓ credentials.json loaded")
            self._creds_lbl.setStyleSheet("color: #10b981; font-size: 12px;")

    def _connect(self):
        if not CREDS_FILE.exists():
            self._warn.setText("Please load your credentials.json first."); self._warn.show(); return
        self.proceed.emit()

# ══════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Daily Scheduler")
        self.resize(1300, 860)
        self.setMinimumSize(960, 620)

        self._creds        = None
        self._cal_by_date: Dict[str, List[Dict]] = {}
        self._fetched_keys: set = set()
        self._cal_threads: List[QThread] = []
        self._all_acts:    List[Dict] = load_all_activities()
        self._cur_date:    date = date.today()
        self._view         = "day"
        self._ai_visible   = False
        # notifications
        self._tray         = None
        self._notify_on    = True
        self._notified:    set = set()     # (block_id, startMin) already announced today
        self._notified_day = date.today().isoformat()
        self._really_quit  = False
        self._tray_hinted  = False

        self.setStyleSheet(f"QMainWindow {{ background: {C_BG.name()}; }}")
        self.setWindowIcon(self._make_app_icon())

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setSpacing(0); root.setContentsMargins(0,0,0,0)

        self._stack = QStackedWidget(); root.addWidget(self._stack)

        # Setup page
        self._setup_page = SetupWidget()
        self._setup_page.proceed.connect(self._boot)
        self._stack.addWidget(self._setup_page)

        # App page
        self._app_page = QWidget()
        self._build_app(self._app_page)
        self._stack.addWidget(self._app_page)

        # Auto-boot if creds exist
        if CREDS_FILE.exists():
            self._boot()

    # ── App page layout ────────────────────────────────────────────────────
    def _build_app(self, parent):
        lay = QVBoxLayout(parent); lay.setSpacing(0); lay.setContentsMargins(0,0,0,0)
        lay.addWidget(self._build_header())

        body    = QWidget()
        body_l  = QHBoxLayout(body); body_l.setSpacing(0); body_l.setContentsMargins(0,0,0,0)

        # Day view — timeline in a scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {C_BG.name()}; }}")
        self._timeline = TimelineWidget()
        self._timeline.block_create_req.connect(self._on_block_create)
        self._timeline.activity_delete_req.connect(self._delete_activity)
        self._timeline.activity_edit_req.connect(self._edit_activity)
        self._timeline.activity_changed.connect(self._commit_activity_change)
        self._scroll.setWidget(self._timeline)

        # Month / year views
        self._month_view = MonthViewWidget()
        self._month_view.day_clicked.connect(self._goto_date)
        self._year_view = YearViewWidget()
        self._year_view.day_clicked.connect(self._goto_date)
        self._year_scroll = QScrollArea()
        self._year_scroll.setWidgetResizable(True)
        self._year_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {C_BG.name()}; }}")
        self._year_scroll.setWidget(self._year_view)

        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(self._scroll)       # 0 — day
        self._view_stack.addWidget(self._month_view)   # 1 — month
        self._view_stack.addWidget(self._year_scroll)  # 2 — year
        body_l.addWidget(self._view_stack, 1)

        # Sidebar
        self._sidebar = SidebarWidget()
        body_l.addWidget(self._sidebar)

        # AI Panel (hidden by default) — wired to edit the schedule via tools
        self._ai_panel = AIPanel(self._ai_ctx)
        self._ai_panel.execute_tool = self._ai_execute
        self._ai_panel.hide()
        body_l.addWidget(self._ai_panel)

        lay.addWidget(body, 1)

        # Status bar
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setStyleSheet(
            f"color: {C_MUTED.name()}; font-size: 11px; padding: 3px 14px;"
            f"border-top: 1px solid {C_BORDER.name()}; background: {C_SURFACE.name()};")
        lay.addWidget(self._status_lbl)

        # Refresh now-line every 30 s
        self._now_timer = QTimer(self)
        self._now_timer.timeout.connect(self._timeline.update)
        self._now_timer.start(30_000)

        # Tray icon + block-start notifications
        self._setup_tray()
        self._notify_timer = QTimer(self)
        self._notify_timer.timeout.connect(self._check_block_starts)
        self._notify_timer.start(20_000)   # check every 20 s

        self._refresh_view()

    def _build_header(self) -> QWidget:
        hdr = QWidget(); hdr.setFixedHeight(52)
        hdr.setStyleSheet(f"background:{C_SURFACE.name()}; border-bottom:1px solid {C_BORDER.name()};")
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(16,0,16,0); hl.setSpacing(8)

        def hbtn(text, checked=False):
            b = QPushButton(text)
            b.setCheckable(checked)
            b.setStyleSheet(f"""
                QPushButton {{ background:{C_SURF2.name()}; border:1px solid {C_BORDER.name()};
                color:{C_MUTED.name()}; padding:5px 13px; border-radius:6px; font-size:12px; }}
                QPushButton:hover {{ color:{C_TEXT.name()}; border-color:{C_BORDER2.name()}; }}
                QPushButton:checked {{ background:rgba(124,111,247,.15);
                border-color:rgba(124,111,247,.5); color:{C_ACCENT.name()}; }}
            """)
            return b

        logo = QLabel("◈ Daily Scheduler")
        logo.setStyleSheet(f"font-size:15px; font-weight:bold; color:{C_ACCENT.name()};")
        hl.addWidget(logo)

        prev_b = hbtn("‹"); prev_b.setFixedWidth(30)
        prev_b.clicked.connect(lambda: self._nav(-1))
        today_b = hbtn("Today")
        today_b.clicked.connect(lambda: self._goto_date(date.today()))
        next_b = hbtn("›"); next_b.setFixedWidth(30)
        next_b.clicked.connect(lambda: self._nav(1))
        hl.addWidget(prev_b); hl.addWidget(today_b); hl.addWidget(next_b)

        self._date_lbl = QLabel(datetime.now().strftime("%A, %B %d, %Y"))
        self._date_lbl.setStyleSheet(f"color:{C_TEXT.name()}; font-size:13px; font-weight:bold;")
        hl.addWidget(self._date_lbl); hl.addStretch()

        self._view_btns = {}
        for vid, vlbl in [("day", "Day"), ("month", "Month"), ("year", "Year")]:
            b = hbtn(vlbl, checked=True)
            b.setChecked(vid == "day")
            b.clicked.connect(lambda _, v=vid: self._set_view(v))
            self._view_btns[vid] = b
            hl.addWidget(b)

        self._ai_btn = hbtn("🤖 AI", checked=True)
        self._ai_btn.clicked.connect(self._toggle_ai)
        hl.addWidget(self._ai_btn)

        self._refresh_btn = hbtn("↺ Refresh")
        self._refresh_btn.clicked.connect(self._refresh_cal)
        hl.addWidget(self._refresh_btn)

        self._auth_btn = QPushButton("👤 Connect Google")
        self._auth_btn.setStyleSheet(f"""
            QPushButton {{ background:{C_ACCENT.name()}; color:white; padding:5px 13px;
            border-radius:6px; font-size:12px; border:none; }}
            QPushButton:hover {{ background:{C_ACCENT2.name()}; }}
        """)
        self._auth_btn.clicked.connect(self._auth_google)
        hl.addWidget(self._auth_btn)

        return hdr

    # ── Boot ───────────────────────────────────────────────────────────────
    def _boot(self):
        self._stack.setCurrentIndex(1)
        if CREDS_FILE.exists():
            self._auth_google()

    def _auth_google(self):
        self._set_status("Connecting to Google Calendar…")
        self._auth_t = GoogleAuthThread()
        self._auth_t.done.connect(self._on_auth)
        self._auth_t.error.connect(lambda e: self._set_status(f"Auth error: {e}", True))
        self._auth_t.start()

    def _on_auth(self, creds):
        self._creds = creds
        self._auth_btn.setText("● Connected")
        self._auth_btn.setStyleSheet("""
            QPushButton { background: transparent; border: 1px solid #262636;
            color: #10b981; padding: 5px 13px; border-radius: 6px; font-size: 12px; }
        """)
        self._set_status("Google connected. Fetching events…")
        self._refresh_cal()

    def _refresh_cal(self):
        if not self._creds:
            self._set_status("Not connected to Google Calendar."); return
        self._fetched_keys.clear()
        self._cal_by_date.clear()
        self._ensure_cal_for_view()

    def _ensure_cal_for_view(self):
        """Fetch Google events covering the visible range, once per range."""
        if not self._creds:
            return
        d = self._cur_date
        if self._view == "year":
            key, start, end = f"y{d.year}", date(d.year, 1, 1), date(d.year + 1, 1, 1)
        else:
            key   = f"m{d.year}-{d.month}"
            start = date(d.year, d.month, 1)
            end   = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        if key in self._fetched_keys:
            return
        self._fetched_keys.add(key)
        self._set_status("Fetching calendar…")
        t = CalFetchThread(self._creds, start, end)
        t.done.connect(self._on_cal)
        t.error.connect(lambda e, k=key: (self._fetched_keys.discard(k),
                                          self._set_status(e, True)))
        t.finished.connect(lambda t=t: t in self._cal_threads and self._cal_threads.remove(t))
        self._cal_threads.append(t)
        t.start()

    def _on_cal(self, by_date: dict):
        self._cal_by_date.update(by_date)
        self._refresh_view()
        n = sum(len(v) for v in by_date.values())
        self._set_status(f"Synced {n} event{'s' if n != 1 else ''}")

    # ── Per-day data accessors ─────────────────────────────────────────────
    def _day_cal(self, d: Optional[date] = None) -> List[Dict]:
        d = d or self._cur_date
        return self._cal_by_date.get(d.isoformat(), [])

    def _day_acts(self, d: Optional[date] = None) -> List[Dict]:
        ds = (d or self._cur_date).isoformat()
        return [a for a in self._all_acts if a.get("date") == ds]

    def _match_acts(self, title_q: str, ds: Optional[str]) -> List[Dict]:
        """Fuzzy title match over user blocks: emoji/punctuation-insensitive,
        case-insensitive substring in either direction. ds=None searches all dates."""
        q = norm_title(title_q)
        if not q:
            return []
        pool = self._all_acts if ds is None else \
               [a for a in self._all_acts if a.get("date") == ds]
        return [a for a in pool
                if q in norm_title(a.get("title", ""))
                or norm_title(a.get("title", "")) in q]

    # ── Navigation ─────────────────────────────────────────────────────────
    def _set_view(self, v: str):
        self._view = v
        for k, b in self._view_btns.items():
            b.setChecked(k == v)
        self._view_stack.setCurrentIndex({"day": 0, "month": 1, "year": 2}[v])
        self._ensure_cal_for_view()
        self._refresh_view()

    def _goto_date(self, d: date):
        self._cur_date = d
        if self._view != "day":
            self._set_view("day")
        else:
            self._ensure_cal_for_view()
            self._refresh_view()

    def _nav(self, step: int):
        d = self._cur_date
        if self._view == "day":
            self._cur_date = d + timedelta(days=step)
        elif self._view == "month":
            m = d.month + step
            y = d.year + (m - 1) // 12
            m = (m - 1) % 12 + 1
            self._cur_date = date(y, m, min(d.day, _cal.monthrange(y, m)[1]))
        else:
            y = d.year + step
            self._cur_date = date(y, d.month, min(d.day, _cal.monthrange(y, d.month)[1]))
        self._ensure_cal_for_view()
        self._refresh_view()

    # ── View refresh ───────────────────────────────────────────────────────
    def _refresh_view(self):
        d = self._cur_date
        if self._view == "day":
            self._date_lbl.setText(d.strftime("%A, %B %d, %Y"))
            cal_ev = self._day_cal()
            acts   = self._day_acts()
            self._timeline.set_data(cal_ev, acts, d)
            self._sidebar.update_summary(cal_ev, acts)
            if d == date.today():
                now_min = datetime.now().hour * 60 + datetime.now().minute
                y = max(0, min_to_y(max(now_min - 60, DAY_START)))
            else:
                y = 0
            QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(y))
        elif self._view == "month":
            self._date_lbl.setText(d.strftime("%B %Y"))
            ev: Dict[str, List[Dict]] = {}
            for ds, lst in self._cal_by_date.items():
                if lst:
                    ev.setdefault(ds, []).extend(lst)
            for a in self._all_acts:
                ev.setdefault(a.get("date", ""), []).append(a)
            self._month_view.set_month(d.year, d.month, ev)
        else:
            self._date_lbl.setText(str(d.year))
            busy = {k for k, v in self._cal_by_date.items() if v} | \
                   {a.get("date") for a in self._all_acts}
            self._year_view.set_year(d.year, busy)

    # ── Activity actions ───────────────────────────────────────────────────
    def _on_block_create(self, s, e):
        dlg = AddActivityDialog(s, e, self._sidebar.selected_type,
                                self._cur_date.isoformat(), parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.result_activity:
            self._all_acts.append(dlg.result_activity)
            save_all_activities(self._all_acts)
            self._refresh_view()

    def _edit_activity(self, aid):
        act = next((a for a in self._all_acts if a["id"] == aid), None)
        if not act:
            return
        dlg = AddActivityDialog(act["startMin"], act["endMin"], act["type"],
                                existing=act, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.result_deleted:
            self._all_acts = [a for a in self._all_acts if a["id"] != aid]
        elif dlg.result_activity:
            self._all_acts = [dlg.result_activity if a["id"] == aid else a
                              for a in self._all_acts]
        save_all_activities(self._all_acts)
        self._refresh_view()

    def _commit_activity_change(self, aid, start, end):
        """Apply a drag move/resize to an existing block."""
        for a in self._all_acts:
            if a["id"] == aid:
                a["startMin"] = max(DAY_START, int(start))
                a["endMin"]   = min(DAY_END, max(int(end), a["startMin"] + self._timeline.SNAP))
                break
        save_all_activities(self._all_acts)
        self._refresh_view()

    def _delete_activity(self, aid):
        self._all_acts = [a for a in self._all_acts if a["id"] != aid]
        save_all_activities(self._all_acts)
        self._refresh_view()

    # ── AI panel ───────────────────────────────────────────────────────────
    def _toggle_ai(self):
        self._ai_visible = not self._ai_visible
        self._ai_panel.setVisible(self._ai_visible)
        self._ai_btn.setChecked(self._ai_visible)

    def _ai_ctx(self):
        return {"cal_events": self._day_cal(),
                "activities": self._day_acts(),
                "view_date":  self._cur_date.isoformat()}

    def _ai_execute(self, name: str, args: Dict) -> str:
        """Run one AI tool call against the schedule. Returns a result string
        that is shown in chat AND fed back to the model."""
        try:
            ds = resolve_date(args.get("date"), self._cur_date)
            if ds is None:
                return (f"Error: couldn't understand the date "
                        f"'{args.get('date')}'. Use Month/Day like 6/14, or 'tomorrow'.")

            if name == "add_block":
                sm = parse_hhmm(str(args["start"]))
                em = parse_hhmm(str(args["end"]))
                if em <= sm:
                    return "Error: end must be after start."
                tid = str(args.get("type", "study"))
                at  = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                title = str(args.get("title") or f"{at['icon']} {at['label']}")
                day_blocks = [b for b in self._all_acts if b.get("date") == ds] + \
                             self._cal_by_date.get(ds, [])
                dur    = em - sm
                placed = find_free_placement(day_blocks, sm, dur)
                if placed is None:
                    return (f"Error: no free {fmt_dur(dur)} slot left on {ds} — the day "
                            f"is full. Rebuild it with replace_day, or use a shorter block.")
                note = ""
                if placed != sm:
                    note = (f" ({fmt_time(sm)} was taken — placed at the nearest free "
                            f"slot instead.)")
                sm, em = placed, placed + dur
                self._all_acts.append({
                    "id": new_id(), "date": ds, "startMin": sm, "endMin": em,
                    "type": at["id"], "color": at["color"], "title": title,
                })
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Added '{title}' on {ds}, {fmt_time(sm)}–{fmt_time(em)}.{note}"

            if name == "delete_block":
                title_q = str(args.get("title", ""))
                if not title_q.strip():
                    return ("Error: 'title' is required. To remove every block on a date, "
                            "call clear_day instead.")
                hits = self._match_acts(title_q, ds)
                if not hits:
                    hits = self._match_acts(title_q, None)   # fall back: search every date
                if not hits:
                    avail = ", ".join(f"'{a['title']}'" for a in self._all_acts
                                      if a.get("date") == ds) or "none"
                    return (f"No editable block matching '{title_q}' on {ds}. "
                            f"Editable blocks that day: {avail}.")
                for a in hits:
                    self._all_acts.remove(a)
                save_all_activities(self._all_acts)
                self._refresh_view()
                return "Deleted: " + ", ".join(f"'{a['title']}' ({a['date']})" for a in hits)

            if name == "move_block":
                title_q = str(args.get("title", ""))
                if not title_q.strip():
                    return "Error: 'title' is required."
                hits = self._match_acts(title_q, ds)
                if not hits:
                    hits = self._match_acts(title_q, None)   # fall back: search every date
                if not hits:
                    avail = ", ".join(f"'{a['title']}' on {a['date']}"
                                      for a in self._all_acts) or "none"
                    return (f"No editable block matching '{title_q}'. "
                            f"Editable blocks: {avail}.")
                if len(hits) > 1:
                    listing = "; ".join(f"'{a['title']}' {a['date']} "
                                        f"{fmt_time(a['startMin'])}" for a in hits[:5])
                    return (f"Ambiguous — {len(hits)} blocks match '{title_q}': "
                            f"{listing}. Use a more specific title.")
                a = hits[0]
                old_dur = a["endMin"] - a["startMin"]
                if args.get("start"):
                    a["startMin"] = parse_hhmm(str(args["start"]))
                    if not args.get("end"):   # only start given → keep the duration
                        a["endMin"] = min(a["startMin"] + old_dur, DAY_END)
                if args.get("end"):
                    a["endMin"] = parse_hhmm(str(args["end"]))
                if args.get("new_date"):
                    nd = resolve_date(args["new_date"], self._cur_date)
                    if nd is None:
                        return f"Error: couldn't understand new_date '{args['new_date']}'."
                    a["date"] = nd
                if args.get("new_title"):
                    a["title"] = str(args["new_title"]).strip()
                if a["endMin"] <= a["startMin"]:
                    a["endMin"] = min(a["startMin"] + 60, DAY_END)
                save_all_activities(self._all_acts)
                self._refresh_view()
                others = [b for b in self._all_acts
                          if b is not a and b.get("date") == a["date"]] + \
                         self._cal_by_date.get(a["date"], [])
                ov = [b["title"] for b in others
                      if b["startMin"] < a["endMin"] and b["endMin"] > a["startMin"]]
                warn = (f" Warning: now overlaps {', '.join(repr(t) for t in ov[:3])} — "
                        f"consider different times.") if ov else ""
                return (f"Moved '{a['title']}' to {a['date']}, "
                        f"{fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}.{warn}")

            if name == "clear_day":
                n = sum(1 for a in self._all_acts if a.get("date") == ds)
                if not n:
                    return f"Nothing editable on {ds} to clear."
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds]
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Cleared {n} block(s) from {ds}."

            if name == "copy_day":
                src = resolve_date(args.get("from_date"), self._cur_date)
                dst = resolve_date(args.get("to_date"), self._cur_date)
                if src is None or dst is None:
                    return ("Error: couldn't understand the date(s). Use Month/Day "
                            "like 6/14, or 'tomorrow'.")
                if src == dst:
                    return "Error: source and target dates are the same."
                source = [a for a in self._all_acts if a.get("date") == src]
                if not source:
                    return f"Nothing editable on {src} to copy."
                merge = bool(args.get("merge"))
                copies = [{
                    "id": new_id(), "date": dst,
                    "startMin": a["startMin"], "endMin": a["endMin"],
                    "type": a["type"], "color": a["color"], "title": a["title"],
                } for a in source]
                if merge:
                    kept = [a for a in self._all_acts if a.get("date") == dst]
                    merged, n_adj, n_drop = sequentialize(kept + copies)
                    self._all_acts = [a for a in self._all_acts if a.get("date") != dst] + merged
                    note = (f" ({n_adj} shifted to avoid overlaps.)" if n_adj else "")
                else:
                    self._all_acts = [a for a in self._all_acts if a.get("date") != dst] + copies
                    note = ""
                save_all_activities(self._all_acts)
                self._refresh_view()
                return f"Copied {len(copies)} block(s) from {src} to {dst}.{note}"

            if name == "shift_blocks":
                mins = 0
                try:
                    if args.get("minutes") not in (None, ""):
                        mins += int(float(args["minutes"]))
                    if args.get("hours") not in (None, ""):
                        mins += 60 * int(float(args["hours"]))
                except (TypeError, ValueError):
                    return "Error: 'minutes' must be a number (positive = later, negative = earlier)."
                if not mins:
                    return "Error: give 'minutes' — positive = later, negative = earlier (120 = 2h later)."
                acts = [a for a in self._all_acts if a.get("date") == ds]
                if not acts:
                    return f"No editable blocks on {ds} to shift."
                for a in acts:
                    dur = a["endMin"] - a["startMin"]
                    ns  = max(DAY_START, min(a["startMin"] + mins, DAY_END - dur))
                    a["startMin"], a["endMin"] = ns, ns + dur
                # clamping at the day edges can pile blocks up — de-overlap the result
                fixed, n_adj, n_drop = sequentialize(acts)
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + fixed
                save_all_activities(self._all_acts)
                self._refresh_view()
                direction = "later" if mins > 0 else "earlier"
                out = f"Shifted {len(fixed)} block(s) on {ds} {abs(mins)} minutes {direction}."
                if n_adj:
                    out += f" ({n_adj} adjusted at the day edges.)"
                if n_drop:
                    out += f" ({n_drop} dropped — no longer fit in the day.)"
                return out

            if name == "replace_day":
                raw = args.get("blocks")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        return "Error: 'blocks' must be a list of {start, end, title, type}."
                if not isinstance(raw, list) or not raw:
                    return "Error: 'blocks' must be a non-empty list of {start, end, title, type}."
                new_acts, skipped = [], 0
                for b in raw:
                    try:
                        sm = parse_hhmm(str(b["start"]))
                        em = parse_hhmm(str(b["end"]))
                        if em <= sm:
                            raise ValueError("end before start")
                        tid = str(b.get("type", "study"))
                        at  = next((t for t in ACTIVITY_TYPES if t["id"] == tid), ACTIVITY_TYPES[0])
                        new_acts.append({
                            "id": new_id(), "date": ds, "startMin": sm, "endMin": em,
                            "type": at["id"], "color": at["color"],
                            "title": str(b.get("title") or at["label"]),
                        })
                    except Exception:
                        skipped += 1
                if not new_acts:
                    return "Error: none of the blocks were valid (need start, end as 24h HH:MM, title)."
                new_acts, n_adj, n_drop = sequentialize(new_acts)
                if not new_acts:
                    return "Error: the blocks don't fit within the day (7am–11pm)."
                self._all_acts = [a for a in self._all_acts if a.get("date") != ds] + new_acts
                save_all_activities(self._all_acts)
                self._refresh_view()
                lines = ", ".join(f"'{a['title']}' {fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}"
                                  for a in new_acts)
                out = f"Replaced {ds} with {len(new_acts)} blocks: {lines}."
                if n_adj:
                    out += f" ({n_adj} shifted to remove overlaps.)"
                if n_drop:
                    out += f" ({n_drop} dropped — didn't fit before 11pm.)"
                if skipped:
                    out += f" ({skipped} invalid block(s) skipped.)"
                return out

            if name == "list_blocks":
                lines = []
                for ev in sorted(self._cal_by_date.get(ds, []), key=lambda x: x["startMin"]):
                    lines.append(f"[calendar] {ev['title']}: "
                                 f"{fmt_time(ev['startMin'])}–{fmt_time(ev['endMin'])}")
                day_acts = [x for x in self._all_acts if x.get("date") == ds]
                for a in sorted(day_acts, key=lambda x: x["startMin"]):
                    lines.append(f"[{a['type']}] {a['title']}: "
                                 f"{fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}")
                return (f"Schedule for {ds}:\n" + "\n".join(lines)) if lines \
                       else f"Nothing scheduled on {ds}."

            return f"Unknown tool '{name}'."
        except KeyError as ex:
            return f"Error: missing argument {ex}."
        except ValueError as ex:
            return f"Error: {ex}"
        except Exception as ex:
            return f"Error: {ex}"

    # ── Status ─────────────────────────────────────────────────────────────
    def _set_status(self, msg, error=False):
        self._status_lbl.setText(msg)
        color = "#fca5a5" if error else C_MUTED.name()
        self._status_lbl.setStyleSheet(
            f"color:{color}; font-size:11px; padding:3px 14px;"
            f"border-top:1px solid {C_BORDER.name()}; background:{C_SURFACE.name()};")

    # ── Tray icon & notifications ────────────────────────────────────────────
    def _make_app_icon(self) -> QIcon:
        pm = QPixmap(64, 64); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(C_ACCENT); p.setPen(Qt.NoPen)
        p.drawRoundedRect(6, 6, 52, 52, 14, 14)
        p.setBrush(QColor("white"))
        p.drawRoundedRect(16, 14, 32, 6, 2, 2)        # calendar top bar
        p.setFont(QFont("Segoe UI", 20, QFont.Bold)); p.setPen(QColor("white"))
        p.drawText(QRect(0, 14, 64, 50), Qt.AlignCenter, "◈")
        p.end()
        return QIcon(pm)

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self._make_app_icon(), self)
        self._tray.setToolTip("Daily Scheduler")
        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{ background: {C_SURFACE.name()}; color: {C_TEXT.name()};
                     border: 1px solid {C_BORDER2.name()}; padding: 4px; }}
            QMenu::item {{ padding: 6px 16px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {C_SURF2.name()}; }}
        """)
        open_act = menu.addAction("Open Daily Scheduler")
        open_act.triggered.connect(self._show_from_tray)
        self._notify_act = menu.addAction("Notify when blocks start")
        self._notify_act.setCheckable(True)
        self._notify_act.setChecked(True)
        self._notify_act.toggled.connect(lambda v: setattr(self, "_notify_on", v))
        test_act = menu.addAction("Test notification")
        test_act.triggered.connect(self._test_notification)
        menu.addSeparator()
        self._startup_act = menu.addAction("Start with Windows")
        self._startup_act.setCheckable(True)
        self._startup_act.setChecked(is_startup_enabled())
        self._startup_act.toggled.connect(self._toggle_startup)
        menu.addSeparator()
        quit_act = menu.addAction("Quit")
        quit_act.triggered.connect(self._quit_app)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def _test_notification(self):
        if self._tray:
            self._tray.showMessage(
                "✓ Notifications are working",
                "You'll get a toast like this when a block starts.",
                self._make_app_icon(), 8000)

    def _toggle_startup(self, enabled):
        ok = set_startup(enabled)
        if not ok:
            # revert the checkbox to the true state without re-firing this handler
            self._startup_act.blockSignals(True)
            self._startup_act.setChecked(is_startup_enabled())
            self._startup_act.blockSignals(False)
            if self._tray:
                self._tray.showMessage("Couldn't update startup setting",
                    "Windows blocked the change.", self._make_app_icon(), 5000)
            return
        if self._tray:
            msg = ("Daily Scheduler will open in the tray when Windows starts "
                   "(the AI server stays off until you start it)."
                   if enabled else "Removed from Windows startup.")
            self._tray.showMessage("Startup setting updated", msg,
                                   self._make_app_icon(), 5000)

    def _quit_app(self):
        self._really_quit = True
        if self._tray:
            self._tray.hide()
        QApplication.quit()

    NOTIFY_WINDOW = 2   # minutes — only notify a block starting right around now

    def _check_block_starts(self):
        """Notify only for blocks on TODAY that are starting right now (within a small
        window). Using a tight window — rather than 'anything since the last check' —
        means a forward clock jump (waking from sleep, manual time change) can't replay
        a backlog of notifications all at once; only a genuinely-now block fires."""
        if not self._tray or not self._notify_on:
            return
        now = datetime.now()
        now_min = now.hour * 60 + now.minute
        today = date.today().isoformat()
        if today != self._notified_day:       # new day → forget yesterday's notifications
            self._notified.clear()
            self._notified_day = today
        for b in self._all_acts:
            if b.get("date") != today:
                continue
            sm = b["startMin"]
            key = (b["id"], sm)
            if key in self._notified:
                continue
            if now_min - self.NOTIFY_WINDOW <= sm <= now_min:
                self._notified.add(key)
                self._tray.showMessage(
                    f"▶ {b['title']}",
                    f"Starting now · {fmt_time(b['startMin'])} – {fmt_time(b['endMin'])}",
                    self._make_app_icon(), 12000)

    def closeEvent(self, ev):
        # Closing the window keeps the app alive in the tray so reminders still fire.
        # Without a tray (or on explicit Quit), really exit.
        if self._really_quit or not self._tray:
            ev.accept()
            QApplication.quit()
            return
        ev.ignore()
        self.hide()
        if not self._tray_hinted:
            self._tray_hinted = True
            self._tray.showMessage(
                "Daily Scheduler is still running",
                "It stays in the tray so it can remind you when blocks start. "
                "Right-click the tray icon to quit.",
                self._make_app_icon(), 6000)

# ── Entry point ────────────────────────────────────────────────────────────
def main():
    # Register an explicit AppUserModelID so Windows shows our tray toasts as banners
    # (without this, Qt balloon notifications are silently dropped into the action center).
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "DailyScheduler.Planner.1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Daily Scheduler")
    app.setStyle("Fusion")
    # Keep running in the tray after the window is closed, so reminders still fire.
    app.setQuitOnLastWindowClosed(False)

    pal = app.palette()
    pal.setColor(QPalette.Window,          C_BG)
    pal.setColor(QPalette.WindowText,      C_TEXT)
    pal.setColor(QPalette.Base,            C_SURF2)
    pal.setColor(QPalette.AlternateBase,   C_SURFACE)
    pal.setColor(QPalette.Text,            C_TEXT)
    pal.setColor(QPalette.Button,          C_SURFACE)
    pal.setColor(QPalette.ButtonText,      C_TEXT)
    pal.setColor(QPalette.Highlight,       C_ACCENT)
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ToolTipBase,     C_SURF2)
    pal.setColor(QPalette.ToolTipText,     C_TEXT)
    app.setPalette(pal)

    win = MainWindow()

    # Centre on primary screen
    geo = app.primaryScreen().availableGeometry()
    win.move((geo.width() - win.width()) // 2, (geo.height() - win.height()) // 2)

    # Launched at login (--startup): stay hidden in the tray instead of popping the
    # window. Fall back to showing it if there's no tray to live in.
    start_hidden = "--startup" in sys.argv
    if start_hidden and win._tray is not None:
        if not win._tray_hinted:
            win._tray_hinted = True
            win._tray.showMessage(
                "Daily Scheduler is running",
                "Open it from the tray icon. It'll remind you when blocks start.",
                win._make_app_icon(), 5000)
    else:
        win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
