"""Daily Scheduler — Ollama client, AI tools, prompts, model profiles.

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

import json
import os
import platform
import subprocess
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

import requests
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QDialog,
)
from PySide6.QtCore import (
    QThread, Signal,
)

import core
import theme
from core import ACTIVITY_TYPES, CHAT_SAVE_MIN_SEC, load_settings

OLLAMA_URL  = "http://localhost:11434"
# Curated picks that fit a ~16 GB GPU and are strong at tool-calling (this app is
# tool-heavy). Keys are ollama pull tags; shown in the model picker alongside
# whatever `ollama list` reports. `when` is user-facing guidance in Settings /
# the AI panel tooltip — keep each blurb one short paragraph.
MODEL_PROFILES = {
    "qwen3:14b": {
        "badge": "★ Best everyday",
        "vram": "~10 GB",
        "disk": "~9.3 GB",
        "when": (
            "Default recommendation. Reliable tool-calling with context headroom "
            "on 12–16 GB GPUs — use this as your daily driver for planning and edits."
        ),
    },
    "mistral-small3.1:24b": {
        "badge": "Strongest (tight fit)",
        "vram": "~15 GB",
        "disk": "~15 GB",
        "when": (
            "Excellent tool-calling when you want the best quality. Needs ~15 GB "
            "VRAM — a tight fit on 16 GB cards; unload other GPU apps first."
        ),
    },
    "qwen2.5:14b": {
        "badge": "Solid fallback",
        "vram": "~10 GB",
        "disk": "~9 GB",
        "when": (
            "Previous default — still very capable at tools. Use if qwen3 "
            "misbehaves or you already have it pulled."
        ),
    },
    "gpt-oss:20b": {
        "badge": "OpenAI open weights",
        "vram": "~13–14 GB",
        "disk": "~13 GB",
        "when": (
            "OpenAI's open MoE model. Capable generalist; verify tool-calling "
            "in-app on a few plan/edit requests before trusting multi-step rebuilds."
        ),
    },
    "deepseek-r1:14b": {
        "badge": "Deep reasoning",
        "vram": "~10 GB",
        "disk": "~9 GB",
        "when": (
            "Reasoning model that thinks before acting — useful for complex "
            "\"plan my week\" questions, but slower and may narrate instead of "
            "calling tools. The app strips its <think> blocks automatically."
        ),
    },
    "gemma4": {
        "badge": "Try / verify first",
        "vram": "~10 GB",
        "disk": "~9.6 GB",
        "when": (
            "Google's Gemma 4 (default tag ≈ e4b). Capable with native tools, but "
            "less battle-tested here than Qwen — try a few plan/edit requests "
            "before making it your daily driver."
        ),
    },
    "glm-4.7-flash": {
        "badge": "Large MoE (needs VRAM)",
        "vram": "~16+ GB",
        "disk": "~19 GB",
        "when": (
            "30B-class MoE — strong and relatively fast when it fits fully on GPU. "
            "Default quant is ~19 GB on disk, so 16 GB cards may offload to RAM "
            "(slower). Verify tool-calling before bulk schedule rebuilds."
        ),
    },
}
RECOMMENDED_MODELS = list(MODEL_PROFILES.keys())

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


def default_ollama_models_dir() -> Path:
    """Ollama's usual models root when OLLAMA_MODELS is unset."""
    # OLLAMA_MODELS overrides; else models live under OLLAMA_HOME or ~/.ollama
    env = (os.environ.get("OLLAMA_MODELS") or "").strip()
    if env:
        return Path(env).expanduser()
    home = (os.environ.get("OLLAMA_HOME") or "").strip()
    base = Path(home).expanduser() if home else (Path.home() / ".ollama")
    return base / "models"

def resolve_ollama_models_dir(settings: Optional[Dict] = None) -> Path:
    """Configured models folder, or Ollama's default path."""
    raw = ""
    if settings is not None:
        raw = str(settings.get("ollama_models_dir") or "").strip()
    if not raw:
        try:
            raw = str(load_settings().get("ollama_models_dir") or "").strip()
        except Exception:
            raw = ""
    if raw:
        return Path(raw).expanduser()
    return default_ollama_models_dir()

def ollama_env(settings: Optional[Dict] = None) -> Dict[str, str]:
    """Environment for `ollama serve` / pull: optional OLLAMA_MODELS override."""
    env = os.environ.copy()
    raw = ""
    if settings is not None:
        raw = str(settings.get("ollama_models_dir") or "").strip()
    else:
        try:
            raw = str(load_settings().get("ollama_models_dir") or "").strip()
        except Exception:
            pass
    if raw:
        p = Path(raw).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        env["OLLAMA_MODELS"] = str(p)
    return env

def start_ollama(settings: Optional[Dict] = None):
    """Launch the local Ollama server (detached). Returns (ok, message).
    If settings include ollama_models_dir, set OLLAMA_MODELS so pulls land there.
    Only applies when *this app* starts the server — a tray/service Ollama already
    running keeps its own path until restarted."""
    try:
        env = ollama_env(settings)
        if platform.system() == "Windows":
            DETACHED = 0x00000008  # DETACHED_PROCESS
            NO_WIN   = 0x08000000  # CREATE_NO_WINDOW
            subprocess.Popen(
                ["ollama", "serve"],
                env=env,
                creationflags=DETACHED | NO_WIN,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        msg = "Starting Ollama…"
        mid = str((settings or {}).get("ollama_models_dir") or "").strip()
        if mid:
            msg += f"\nModels → {Path(mid).expanduser()}"
        return True, msg
    except FileNotFoundError:
        return False, "Ollama not found on PATH.\nInstall it from https://ollama.com/download"
    except Exception as ex:
        return False, str(ex)


def list_ollama_models() -> List[str]:
    """Installed model tags via the Ollama HTTP API (best-effort; [] on any failure).
    Used to populate the model picker alongside the curated RECOMMENDED_MODELS.

    Uses GET /api/tags, NOT the `ollama list` CLI. At Windows sign-in the CLI spawns a
    child (the server/runner) that inherits this process's stdout pipe, so subprocess
    cleanup blocks in the pipe reader thread until that inherited handle closes — which
    it never does — hanging indefinitely and defeating the timeout. That hang ran inside
    AIPanel construction, so MainWindow.__init__ never finished and the app launched with
    NO WINDOW (process alive in Task Manager). The HTTP call spawns no subprocess, fails
    fast (connection refused) when the server is down, and is hard-bounded by `timeout`."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        if not r.ok:
            return []
        return [m["name"] for m in r.json().get("models", []) if m.get("name")]
    except Exception:
        return []

def _model_tag_key(tag: str) -> str:
    """Normalize for install checks: 'qwen3:14b' and 'qwen3:14b:latest' match."""
    t = (tag or "").strip().lower()
    if t.endswith(":latest"):
        t = t[:-7]
    return t

def model_is_installed(tag: str, installed: Optional[List[str]] = None) -> bool:
    """True if `tag` appears in the local Ollama library (best-effort)."""
    if not tag or not str(tag).strip():
        return False
    have = installed if installed is not None else list_ollama_models()
    want = _model_tag_key(tag)
    # EXACT match only (after :latest normalization). Ollama resolves tags
    # literally — with only 'deepseek-r1:14b' installed, running 'deepseek-r1'
    # means ':latest' and 404s, so a prefix match here would show "Installed"
    # for a model that fails at chat time (and disable the ⬇ pull button).
    return want in {_model_tag_key(m) for m in have}

class OllamaPullThread(QThread):
    """Stream POST /api/pull for one model tag. Progress is a short status string."""
    progress = Signal(str)
    finished_ok = Signal(str)   # model tag
    failed = Signal(str)

    def __init__(self, model: str, parent=None):
        super().__init__(parent)
        self.model = (model or "").strip()
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if not self.model:
            self.failed.emit("No model name."); return
        try:
            self.progress.emit(f"Pulling {self.model}…")
            # Long read timeout: large models take many minutes; Stop cancels via _stop.
            resp = requests.post(
                f"{OLLAMA_URL}/api/pull",
                json={"name": self.model, "stream": True},
                stream=True, timeout=(10, 3600),
            )
            if resp.status_code == 404:
                self.failed.emit(f"Model '{self.model}' not found on the Ollama library.")
                return
            resp.raise_for_status()
            last = ""
            for line in resp.iter_lines():
                if self._stop:
                    self.failed.emit("Pull cancelled."); return
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                st = data.get("status") or ""
                completed = data.get("completed")
                total = data.get("total")
                if total and completed is not None and total > 0:
                    pct = min(100, int(100 * completed / total))
                    mb_c = completed / (1024 * 1024)
                    mb_t = total / (1024 * 1024)
                    msg = f"{st or 'downloading'}  {pct}%  ({mb_c:.0f}/{mb_t:.0f} MB)"
                else:
                    msg = st or "working…"
                if msg != last:
                    self.progress.emit(msg); last = msg
                if data.get("error"):
                    self.failed.emit(str(data["error"])); return
            self.finished_ok.emit(self.model)
        except requests.exceptions.ConnectionError:
            self.failed.emit("Can't reach Ollama. Press ▶ to start it, then try again.")
        except Exception as ex:
            self.failed.emit(str(ex))


def strip_think(s: str) -> str:
    """Remove reasoning-model chain-of-thought (<think>…</think>) from streamed
    content. Drops complete blocks and any still-open trailing block, so DeepSeek-R1
    style models don't dump their reasoning into the chat."""
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S)
    i = s.find("<think>")
    return s[:i] if i != -1 else s


# ── AI chat transcript (v3.8.0) ────────────────────────────────────────────
# Survives OOM / process kill so a conversation isn't lost mid-stream. Local
# only under core.DATA_DIR — never log contents (may include schedule talk).
_chat_save_last = 0.0

def _default_chat_histories() -> Dict[str, List[Dict]]:
    return {
        "chat": [{"role": "assistant", "content": AI_GREETING}],
        "plan": [],
        "suggest": [],
    }

def load_chat_histories() -> Dict[str, List[Dict]]:
    """Best-effort restore of the AI panel transcript. Falls back to a fresh
    greeting on any error / missing / corrupt file."""
    out = _default_chat_histories()
    try:
        if not core.CHAT_FILE.exists():
            return out
        raw = json.loads(core.CHAT_FILE.read_text(encoding="utf-8"))
        modes = raw.get("modes") if isinstance(raw, dict) else None
        if not isinstance(modes, dict):
            return out
        for key in ("chat", "plan", "suggest"):
            msgs = modes.get(key)
            if not isinstance(msgs, list):
                continue
            clean = []
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant", "tool_note", "error") and isinstance(content, str):
                    clean.append({"role": role, "content": content})
            if clean:
                out[key] = clean
    except Exception:
        pass
    return out

def save_chat_histories(history: Dict[str, List[Dict]], *, force: bool = False) -> bool:
    """Write the in-memory AI histories to core.CHAT_FILE. Throttled unless force=True
    (turn boundaries / user send always force). Never raises."""
    global _chat_save_last
    try:
        now = time.monotonic()
        if not force and (now - _chat_save_last) < CHAT_SAVE_MIN_SEC:
            return False
        _chat_save_last = now
        payload = {
            "version": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "app_version": core.APP_VERSION,
            "modes": {
                key: [{"role": m.get("role"), "content": m.get("content", "")}
                      for m in (history.get(key) or [])
                      if isinstance(m, dict) and m.get("role") in
                      ("user", "assistant", "tool_note", "error")]
                for key in ("chat", "plan", "suggest")
            },
        }
        core.CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = core.CHAT_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(core.CHAT_FILE)
        return True
    except Exception:
        return False


# ── Memory preflight / friendly OOM text (v3.8.0) ──────────────────────────
def free_ram_gb() -> Optional[float]:
    """Best-effort free/available system RAM in GiB, or None if unknown.
    This is NOT free VRAM — only a rough signal for soft warnings."""
    try:
        if platform.system() == "Linux":
            avail_kb = None
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        avail_kb = int(line.split()[1]); break
                    if line.startswith("MemFree:") and avail_kb is None:
                        avail_kb = int(line.split()[1])
            if avail_kb is not None:
                return avail_kb / (1024 * 1024)
        elif platform.system() == "Windows":
            import ctypes

            class _MEM(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = _MEM()
            st.dwLength = ctypes.sizeof(_MEM)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullAvailPhys / (1024 ** 3)
    except Exception:
        pass
    return None

def model_need_gb(model: str) -> Optional[float]:
    """Rough GB need from MODEL_PROFILES.vram (takes the highest number in the
    string, e.g. '~13–14 GB' → 14). None if unlisted / unparseable."""
    p = model_profile(model)
    if not p:
        return None
    nums = re.findall(r"(\d+(?:\.\d+)?)", p.get("vram") or "")
    if not nums:
        return None
    return max(float(n) for n in nums)

def memory_warning_for(model: str) -> str:
    """Soft preflight blurb, or '' if no concern / unknown. Never hard-blocks."""
    need = model_need_gb(model)
    free = free_ram_gb()
    if need is None or free is None:
        return ""
    # Only warn when free system RAM is clearly under the model's typical footprint.
    # (Free RAM ≠ free VRAM — wording makes that explicit.)
    if free >= need * 0.9:
        return ""
    return (
        f"Heads-up: about {free:.0f} GB system RAM free, and '{model}' typically wants "
        f"~{need:.0f} GB of GPU memory. Free RAM is not the same as free VRAM — if the "
        f"GPU is busy (games, browser, another model), the model may get killed mid-reply. "
        f"Close heavy apps or switch to a smaller model (e.g. qwen3:14b)."
    )

def friendly_stream_error(exc: BaseException, *, got_tokens: bool, model: str) -> str:
    """Human text for mid-stream / connection failures (OOM, server death, etc.)."""
    name = type(exc).__name__
    low = f"{name}: {exc}".lower()
    oomish = any(k in low for k in (
        "connection", "reset", "broken pipe", "chunked", "remote", "aborted",
        "eof", "protocol", "forcibly closed", "remotedisconnected",
    ))
    if got_tokens or oomish:
        return (
            f"The reply was cut off mid-stream"
            f"{' after the model started answering' if got_tokens else ''}.\n\n"
            f"Most often the model process was killed for memory (OOM) or Ollama "
            f"restarted. Try:\n"
            f"  • Unload (⏏) and send again\n"
            f"  • A smaller model (qwen3:14b is the roomy daily driver)\n"
            f"  • Close other GPU apps, then ▶ start Ollama again\n\n"
            f"(Model was '{model}'. Technical: {name})"
        )
    return (
        "Can't reach Ollama. Click the ▶ button to start it,\n"
        "or run 'ollama serve' in a terminal."
    )


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
    models = Signal(list)   # installed tags from the same /api/tags response
    def run(self):
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            if r.ok:
                # Same payload list_ollama_models() parses — reuse it so the UI
                # never needs its own (blocking) HTTP call for install state.
                try:
                    tags = [m.get("name", "") for m in r.json().get("models", [])]
                    self.models.emit([t for t in tags if t])
                except Exception:
                    pass
            self.result.emit(r.ok)
        except Exception:
            self.result.emit(False)


class OllamaThread(QThread):
    token      = Signal(str)
    done       = Signal()
    tool_calls = Signal(list)
    error      = Signal(str)

    def __init__(self, messages, model, tools=None, num_ctx=16384, temperature=0.3):
        super().__init__()
        self.messages    = messages
        self.model       = model
        self.tools       = tools
        self.num_ctx     = num_ctx
        self.temperature = temperature
        self._stop       = False

    def stop(self): self._stop = True

    def run(self):
        got_tokens = False
        try:
            payload = {"model": self.model, "messages": self.messages, "stream": True,
                       "options": {"num_ctx": self.num_ctx,
                                   "temperature": self.temperature, "top_p": 0.9}}
            if self.tools:
                payload["tools"] = self.tools
            # (connect, read) timeouts: fail fast when the server is down, but the
            # read timeout is the max SILENCE before the first streamed byte — and
            # Ollama sends nothing while it loads a model into VRAM, so a cold load
            # of a 24B model can far exceed 120 s. 600 s covers a big cold load;
            # the Stop button still works between chunks once streaming starts.
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat", json=payload,
                stream=True, timeout=(5, 600),
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
            raw, sent = "", 0          # raw = full content; sent = chars already emitted
            for line in resp.iter_lines():
                if self._stop: break
                if not line: continue
                try:
                    data = json.loads(line)
                    msg  = data.get("message") or {}
                    c    = msg.get("content", "")
                    if c:                       # strip <think> reasoning, emit only the delta
                        raw += c
                        vis = strip_think(raw)
                        if len(vis) > sent:
                            self.token.emit(vis[sent:]); sent = len(vis)
                            got_tokens = True
                    if msg.get("tool_calls"):
                        calls.extend(msg["tool_calls"])
                    if data.get("done"): break
                except Exception:
                    pass
            if calls and not self._stop:
                self.tool_calls.emit(calls)
            else:
                self.done.emit()
        except requests.exceptions.Timeout:
            self.error.emit(
                f"Ollama didn't respond in time. If '{self.model}' was cold-loading "
                f"into VRAM it may be ready now — try sending your message again. "
                f"A smaller model also loads (and answers) faster.")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ContentDecodingError,
                BrokenPipeError, ConnectionResetError) as ex:
            self.error.emit(friendly_stream_error(ex, got_tokens=got_tokens, model=self.model))
        except Exception as ex:
            # Mid-stream death often surfaces as a generic RequestException /
            # ProtocolError once the runner is OOM-killed.
            if got_tokens:
                self.error.emit(friendly_stream_error(ex, got_tokens=True, model=self.model))
            else:
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
        "description": "Delete user-created block(s). Identify the block by title and/or by "
                       "its time. To remove ONE specific time slot, pass its start time in "
                       "'at' (e.g. at='14:00' deletes the block starting at 2pm). Combine "
                       "'title' + 'at' to be exact when several blocks share a title.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "title": {"type": "string", "description": "Title (or part of it) of the block to delete."},
            "at":    {"type": "string", "description": "Start time of the specific block to delete, 24h HH:MM (e.g. '14:00'). Targets just that one time slot."},
        }}}},
    {"type": "function", "function": {
        "name": "move_block",
        "description": "Move, resize, or rename ONE user-created block. Identify which block "
                       "with 'title' and/or 'at' (its current start time); use 'at' when "
                       "several blocks share a title. Then set the new time/date/title.",
        "parameters": {"type": "object", "properties": {
            "date":     {"type": "string", "description": "Date the block is currently on (YYYY-MM-DD). Omit for the viewed day."},
            "title":    {"type": "string", "description": "Title (or part) of the block to move."},
            "at":       {"type": "string", "description": "Current start time of the block to move, 24h HH:MM. Use to pick the exact block when titles repeat."},
            "start":    {"type": "string", "description": "NEW start time 24h HH:MM."},
            "end":      {"type": "string", "description": "NEW end time 24h HH:MM."},
            "new_date": {"type": "string", "description": "New date YYYY-MM-DD if moving to another day."},
            "new_title": {"type": "string", "description": "New title, to rename the block."},
        }}}},
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
            "hours":   {"type": "integer", "description": "Optional whole-hour offset, added to 'minutes' (hours=2 → 120 min later). Use either field."},
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
            "from_date": {"type": "string", "description": "Source date (omit = viewed day). Pass the user's own words — a weekday name ('Thursday'), 'today', 6/14, or YYYY-MM-DD."},
            "to_date":   {"type": "string", "description": "Target date. Pass the user's own words — a weekday name ('Thursday'), 'tomorrow', 6/14, or YYYY-MM-DD — NOT a date you worked out yourself; the app resolves it."},
            "merge":     {"type": "boolean", "description": "If true, keep the target's existing blocks and add the copies alongside them. Default false (replace)."},
        }, "required": ["to_date"]}}},
    {"type": "function", "function": {
        "name": "add_recurring",
        "description": "Add the SAME block to multiple days in one call — for repeating "
                       "things like classes or a daily study slot. Specify the days either "
                       "with 'weekdays' (e.g. ['monday','wednesday'], or 'weekdays'/'weekends'/"
                       "'daily') optionally over several 'weeks', or with an explicit 'dates' list.",
        "parameters": {"type": "object", "properties": {
            "title":    {"type": "string"},
            "start":    {"type": "string", "description": "24h HH:MM"},
            "end":      {"type": "string", "description": "24h HH:MM"},
            "type":     {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
            "weekdays": {"type": "array", "items": {"type": "string"},
                          "description": "Weekday names and/or 'weekdays','weekends','daily'. Applied across the next 'weeks' starting from the viewed day."},
            "weeks":    {"type": "integer", "description": "How many weeks for weekday recurrence (default 1, max 8)."},
            "dates":    {"type": "array", "items": {"type": "string"},
                          "description": "Explicit list of dates (YYYY-MM-DD, or 6/14, tomorrow…). Use instead of weekdays for specific days."},
        }, "required": ["start", "end", "title"]}}},
    {"type": "function", "function": {
        "name": "clear_range",
        "description": "Delete editable blocks that fall within a time window on a date "
                       "(e.g. 'clear my afternoon' → 12:00–18:00). Use clear_day for the whole day.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "start": {"type": "string", "description": "Window start 24h HH:MM."},
            "end":   {"type": "string", "description": "Window end 24h HH:MM."},
        }, "required": ["start", "end"]}}},
    {"type": "function", "function": {
        "name": "find_free_time",
        "description": "Read-only: list open gaps (free of editable blocks AND calendar "
                       "events) on a date. Use to answer 'when am I free?' and to choose "
                       "where to place new blocks. Does not modify anything.",
        "parameters": {"type": "object", "properties": {
            "date":     {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "duration": {"type": "integer", "description": "Only return gaps at least this many minutes long."},
            "after":    {"type": "string", "description": "Only consider time after this (24h HH:MM)."},
            "before":   {"type": "string", "description": "Only consider time before this (24h HH:MM)."},
        }}}},
    {"type": "function", "function": {
        "name": "split_block",
        "description": "Split one existing block into focused chunks separated by short "
                       "breaks (pomodoro-style), within its original time span. The focus "
                       "chunks keep the block's type; the breaks are downtime (see break_type). "
                       "Identify the block by title and/or 'at' (start time).",
        "parameters": {"type": "object", "properties": {
            "date":   {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "title":  {"type": "string", "description": "Title (or part) of the block to split."},
            "at":     {"type": "string", "description": "Start time of the block to split, 24h HH:MM."},
            "chunk":  {"type": "integer", "description": "Length of each focus chunk in minutes (default 30)."},
            "break":  {"type": "integer", "description": "Length of each break in minutes (default 5; 0 for none)."},
            "break_type": {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES],
                            "description": "Category for the breaks (default 'free' = rest). A break is rest, not study — don't reuse the work block's type."},
        }}}},
    {"type": "function", "function": {
        "name": "schedule_tasks",
        "description": "INTELLIGENT PLANNING — your main tool for 'plan my day' / 'fit these "
                       "things in'. You supply the tasks (with durations, urgency, and "
                       "preferred time of day from your own reasoning); the app places each "
                       "into a real free slot at reasonable hours, around existing blocks and "
                       "calendar events. It NEVER deletes anything and never overlaps, so it's "
                       "safe to plan around meals/classes the user is keeping. Higher-priority "
                       "tasks get earlier slots.",
        "parameters": {"type": "object", "properties": {
            "date":      {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "day_start": {"type": "string", "description": "Earliest time to schedule (24h HH:MM). Defaults to the user's waking-hours start (and not earlier than now when planning today)."},
            "day_end":   {"type": "string", "description": "Latest time to schedule (24h HH:MM, default 22:00)."},
            "tasks": {"type": "array", "description": "Tasks to place, in any order.",
                "items": {"type": "object", "properties": {
                    "title":    {"type": "string"},
                    "minutes":  {"type": "integer", "description": "How long the task needs."},
                    "type":     {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
                    "priority": {"type": "string", "enum": ["high", "normal", "low"],
                                  "description": "Urgent/important → 'high' (placed earliest)."},
                    "prefer":   {"type": "string", "description": "Preferred time: 'morning'/'afternoon'/'evening' or a time like '15:00'. Optional."},
                }, "required": ["title", "minutes"]}},
        }, "required": ["tasks"]}}},
    {"type": "function", "function": {
        "name": "reflow_from_now",
        "description": "\"I'm running late\" — push the blocks still to come on a day later "
                       "(or earlier) by an offset, leaving past/ongoing blocks alone. Use when "
                       "the user has fallen behind and wants the rest of the day shifted.",
        "parameters": {"type": "object", "properties": {
            "date":    {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "minutes": {"type": "integer", "description": "How far to push upcoming blocks. Positive = later (running behind), negative = earlier (ahead)."},
            "from":    {"type": "string", "description": "Only move blocks starting at/after this time (24h HH:MM). Default: the current time when the day is today, else the start of the day."},
        }, "required": ["minutes"]}}},
    {"type": "function", "function": {
        "name": "plan_for_deadline",
        "description": "Spread work for a deadline across the days leading up to it. Give the "
                       "total time the job needs and (optionally) a session length; the app "
                       "places one focus session per day into free time across the days before "
                       "the deadline, never overlapping existing blocks. Use for 'study 4 hours "
                       "before Friday's exam' or 'plan my essay over the week'. Idempotent — "
                       "re-running doesn't duplicate sessions already placed.",
        "parameters": {"type": "object", "properties": {
            "title":    {"type": "string", "description": "What the work is (e.g. 'Study for chem exam')."},
            "deadline": {"type": "string", "description": "Due date YYYY-MM-DD, or words like 'friday' / '6/20'."},
            "minutes":  {"type": "integer", "description": "Total time the whole job needs, in minutes."},
            "session":  {"type": "integer", "description": "Length of each daily focus session in minutes (default 60)."},
            "type":     {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES], "description": "Activity category (default study)."},
            "start_date": {"type": "string", "description": "First day to start from (YYYY-MM-DD; default today)."},
        }, "required": ["title", "deadline", "minutes"]}}},
    {"type": "function", "function": {
        "name": "week_summary",
        "description": "Read-only: total time per category over a date range (default the week "
                       "containing the viewed day), with a per-day average. Use to answer 'how "
                       "much sleep/study/exercise did I get this week?' and to spot balance "
                       "problems. Modifies nothing.",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string", "description": "Range start (YYYY-MM-DD or words). Omit for the start of the viewed week."},
            "end":   {"type": "string", "description": "Range end (YYYY-MM-DD or words). Omit for the end of the viewed week."},
        }}}},
    {"type": "function", "function": {
        "name": "plan_day",
        "description": "Build a whole day by laying out ORDERED tasks around FIXED anchors "
                       "(meals, workout, wake-up) and calendar events — the reliable way to "
                       "handle 'plan my day: X first then Y, lunch at 13:00, workout at 16:00, "
                       "30-min chunks with breaks'. Give each task its TOTAL focus minutes "
                       "(breaks are EXTRA and NOT counted) and optionally a chunk + break size; "
                       "the app places everything in order from 'start', splits each task into "
                       "chunks separated by breaks, and flows the rest PAST every fixed anchor "
                       "and meeting. REPLACES the day's editable blocks, so include every fixed "
                       "item you want kept. PREFER THIS over hand-building with replace_day "
                       "whenever there's a set order + fixed times + chunking.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "start": {"type": "string", "description": "When tasks begin, 24h HH:MM (default the user's waking-hours start; on today, not before now)."},
            "fixed": {"type": "array", "description": "Anchors placed at exact times that tasks flow around (lunch, workout, wake-up).",
                "items": {"type": "object", "properties": {
                    "title":   {"type": "string"},
                    "start":   {"type": "string", "description": "24h HH:MM"},
                    "minutes": {"type": "integer", "description": "Length in minutes (or give 'end')."},
                    "end":     {"type": "string", "description": "24h HH:MM (alternative to minutes)."},
                    "type":    {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
                }, "required": ["title", "start"]}},
            "tasks": {"type": "array", "description": "Tasks in the ORDER to do them.",
                "items": {"type": "object", "properties": {
                    "title":   {"type": "string"},
                    "minutes": {"type": "integer", "description": "TOTAL focus time for this task — do NOT include break time."},
                    "type":    {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES]},
                    "chunk":   {"type": "integer", "description": "Split into chunks this many minutes long (omit = one solid block)."},
                    "break":   {"type": "integer", "description": "Break minutes between chunks (default 15 when chunk is set; 0 for none)."},
                }, "required": ["title", "minutes"]}},
        }, "required": ["tasks"]}}},
    {"type": "function", "function": {
        "name": "make_room",
        "description": "Add a FIXED appointment at an exact time and shuffle the day's existing "
                       "blocks AROUND it — WITHOUT deleting any. THIS is how to handle 'I have a "
                       "meeting 12:00–13:30, adjust my schedule' or 'something came up at 3pm, "
                       "move things around it'. The appointment (plus optional buffer time) and "
                       "any 'pin'ned blocks stay fixed; every OTHER block keeps its order and "
                       "duration and is shifted to flow around them (and around calendar events). "
                       "Use this instead of add_block (which would just drop the appointment in a "
                       "random free slot) or a chain of move_block calls.",
        "parameters": {"type": "object", "properties": {
            "date":  {"type": "string", "description": "ISO date YYYY-MM-DD. Omit for the viewed day."},
            "title": {"type": "string", "description": "Name of the appointment (e.g. 'College Applications Meeting')."},
            "start": {"type": "string", "description": "Appointment start, 24h HH:MM."},
            "end":   {"type": "string", "description": "Appointment end, 24h HH:MM."},
            "type":  {"type": "string", "enum": [t["id"] for t in ACTIVITY_TYPES], "description": "Category (default 'extra')."},
            "buffer_before": {"type": "integer", "description": "Minutes of transition time to reserve right BEFORE the appointment (added as a Break). Default 0."},
            "buffer_after":  {"type": "integer", "description": "Minutes of transition time to reserve right AFTER the appointment. Default 0."},
            "pin": {"type": "array", "items": {"type": "string"},
                     "description": "Titles of existing blocks to keep FIXED in place (e.g. ['Workout/Break']); everything else flows around them. Optional."},
        }, "required": ["title", "start", "end"]}}},
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

# ── Per-model prompt tuning ─────────────────────────────────────────────────
# Each local model has different failure modes on this tool-heavy task. The base
# system prompt is the same for all; model_guidance() appends an extensively
# detailed, model-specific addendum that targets that family's known weaknesses.
# Common thread: emit NATIVE tool calls (not prose, not printed JSON), use the
# correct single bulk tool, keep exact argument shapes, and verify with list_blocks.

_R1_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — DeepSeek-R1 (reasoning model) ══\n"
    "Your private chain-of-thought is HIDDEN from the user and is stripped out before "
    "anything is shown. Reasoning therefore changes NOTHING on its own — only tool calls "
    "do. Obey these rules exactly:\n"
    "1. THINK BRIEFLY, THEN ACT. Do a short reasoning pass, then stop and act. Do not loop "
    "or re-derive the whole day repeatedly; long reasoning wastes the context window.\n"
    "2. A TOOL CALL IS MANDATORY for any request to add / move / delete / rename / clear / "
    "shift / copy / split / plan / replace. Writing 'I will add…', 'You could…', or showing "
    "the finished schedule as text DOES NOTHING. If you catch yourself describing the change "
    "in prose, STOP and emit the tool call instead.\n"
    "3. USE THE NATIVE FUNCTION-CALL CHANNEL. Never print the call as text, as a JSON object, "
    "as an array, or inside ``` fences. If — and only if — your runtime truly cannot call "
    "functions, output ONE single JSON object {\"name\":\"<tool>\",\"arguments\":{…}} and "
    "absolutely nothing else (no prose, no fences, no <think> around it).\n"
    "4. EXACT ARGUMENT SHAPES (R1 is the most likely to get these wrong):\n"
    "   • Times are STRINGS in 24-hour zero-padded 'HH:MM' — '09:00', '14:30', not '9', "
    "'9am', or 900.\n"
    "   • Dates are 'YYYY-MM-DD', or pass the user's own words ('6/14', 'tomorrow', "
    "'monday'); NEVER invent or change the year.\n"
    "   • schedule_tasks → 'tasks' is an ARRAY of objects, each at least {\"title\":str, "
    "\"minutes\":int}; optional \"type\", \"priority\" (high/normal/low), \"prefer\".\n"
    "   • replace_day → 'blocks' is an ARRAY of {\"start\",\"end\",\"title\",\"type\"}.\n"
    "5. ONE TOOL CALL PER STEP. After each call, READ the result text that comes back, then "
    "decide the next step. When done editing, call list_blocks ONCE to verify, fix anything "
    "wrong, then write ONE short confirmation sentence.\n"
    "6. NEVER chain many add_block calls for a bulk job — use the single matching bulk tool "
    "(schedule_tasks, replace_day, shift_blocks, clear_day, copy_day, add_recurring).\n"
    "7. If genuinely ambiguous, ask ONE short question. But if the user named a time, target "
    "that block with 'at' = its start time; don't ask.\n"
)

_GPTOSS_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — gpt-oss ══\n"
    "1. ACT, DON'T NARRATE. The moment the user asks for a schedule change, call the matching "
    "tool. Do NOT first write an analysis, a numbered plan, or 'Here's what I'll do' — the "
    "tool call IS the action. Keep all reasoning short and low-effort; this is simple "
    "scheduling, not a puzzle.\n"
    "2. NATIVE TOOL CALLS ONLY. Use the function-calling channel. Never emit the call as "
    "prose, as printed JSON, or inside a code block, and never narrate it in an analysis "
    "channel.\n"
    "3. ONE BEST TOOL PER REQUEST. For whole-day or bulk changes use the bulk tool "
    "(schedule_tasks to plan, replace_day to rebuild, shift_blocks to move everything, "
    "clear_day/clear_range to wipe, copy_day to duplicate, add_recurring to repeat) — never "
    "a sequence of single add_block calls.\n"
    "4. EXACT SHAPES. Times = 24-hour 'HH:MM' strings; dates = 'YYYY-MM-DD' or the user's "
    "words (never invent the year). schedule_tasks.tasks and replace_day.blocks are JSON "
    "arrays of objects with the required keys.\n"
    "5. After multi-step edits, verify ONCE with list_blocks, fix if needed, then confirm in "
    "a single sentence — do not restate the whole schedule.\n"
)

_QWEN3_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — Qwen3 ══\n"
    "/no_think\n"
    "Respond directly, WITHOUT an extended reasoning pass (no <think> block) — this is a "
    "simple scheduling task, so decide fast and call the tool. Your tool-calling is strong; "
    "use it decisively.\n"
    "1. DECIDE QUICKLY. This is a straightforward scheduling assistant; don't enumerate many "
    "alternatives or second-guess. Keep any thinking brief, then call the tool.\n"
    "2. A TOOL CALL IS REQUIRED for every add / move / delete / rename / clear / shift / "
    "copy / split / plan / replace request — never just describe the change in words.\n"
    "3. ONE TOOL FOR BULK JOBS: schedule_tasks to plan, replace_day to rebuild from scratch, "
    "shift_blocks to move the whole day, add_recurring for repeats. Don't chain single "
    "add_block calls.\n"
    "4. EXACT SHAPES. Times = zero-padded 24-hour 'HH:MM' strings; dates = 'YYYY-MM-DD' or "
    "the user's words (never invent the year). schedule_tasks.tasks and replace_day.blocks "
    "are arrays of objects.\n"
    "5. Verify with list_blocks after multi-step edits, fix anything wrong, then confirm in "
    "one short sentence.\n"
)

_QWEN25_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — Qwen2.5 ══\n"
    "1. ALWAYS CALL A TOOL for any schedule change (add / move / delete / rename / clear / "
    "shift / copy / split / plan / replace). Prose alone changes nothing — the calendar only "
    "updates through tool calls.\n"
    "2. NATIVE CHANNEL ONLY. Use the function-calling interface; do not print the call as "
    "text, JSON, an array, or inside ``` fences.\n"
    "3. ONE TOOL PER JOB. For bulk or whole-day work use schedule_tasks / replace_day / "
    "shift_blocks / clear_day / copy_day / add_recurring instead of repeated add_block "
    "calls.\n"
    "4. EXACT SHAPES. Times = 24-hour 'HH:MM' strings; dates = 'YYYY-MM-DD' or the user's "
    "words (never invent the year). schedule_tasks.tasks and replace_day.blocks are arrays "
    "of objects.\n"
    "5. Be concise: after verifying with list_blocks, confirm in one short sentence — don't "
    "restate the whole schedule.\n"
)

_GENERIC_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS ══\n"
    "1. ALWAYS call the matching tool for any schedule change; never only describe it.\n"
    "2. Prefer the native tool-calling channel. If your runtime cannot call functions, emit "
    "ONE single JSON object {\"name\":\"<tool>\",\"arguments\":{…}} and nothing else — no "
    "prose, no code fences.\n"
    "3. Use ONE tool for bulk jobs (schedule_tasks / replace_day / shift_blocks / clear_day / "
    "copy_day / add_recurring); never chain single add_block calls.\n"
    "4. EXACT SHAPES. Times = 24-hour 'HH:MM' strings; dates = 'YYYY-MM-DD' or the user's "
    "words (never invent the year). schedule_tasks.tasks and replace_day.blocks are arrays "
    "of objects.\n"
    "5. Verify with list_blocks after multi-step edits, then confirm in one short sentence.\n"
)

_GEMMA_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — Gemma ══\n"
    "You're a capable general model but less battle-tested at tool-calling than Qwen, so be "
    "disciplined and literal:\n"
    "1. ALWAYS emit a real TOOL CALL for any add / move / delete / rename / clear / shift / "
    "copy / split / plan / replace request. Writing out the change, or showing a finished "
    "schedule as text, does NOTHING — only tool calls edit the calendar.\n"
    "2. USE THE NATIVE FUNCTION-CALL CHANNEL. Never print the call as prose, markdown, or inside "
    "``` fences. If — and only if — you truly cannot call a function, output ONE single JSON "
    "object {\"name\":\"<tool>\",\"arguments\":{…}} and nothing else.\n"
    "3. EXACT ARGUMENT SHAPES (Gemma tends to drift here): times are 24-hour zero-padded "
    "'HH:MM' strings ('09:00', '14:30'); dates are 'YYYY-MM-DD' or the user's own words "
    "('6/14', 'tomorrow') — NEVER invent the year. plan_day.tasks / plan_day.fixed / "
    "schedule_tasks.tasks / replace_day.blocks are JSON ARRAYS of objects with the required keys.\n"
    "4. ONE TOOL PER BULK JOB: plan_day to build an ordered day, schedule_tasks to fit tasks, "
    "replace_day to rebuild, shift_blocks to move the whole day, add_recurring to repeat — never "
    "a chain of single add_block calls.\n"
    "5. Keep replies short. After multi-step edits call list_blocks, fix anything in its "
    "CONFLICTS section, then confirm in ONE sentence.\n"
)

_GLM_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — GLM ══\n"
    "You're a fast agentic model — act decisively and keep internal reasoning brief.\n"
    "1. A TOOL CALL IS REQUIRED for every add / move / delete / rename / clear / shift / copy / "
    "split / plan / replace — never just describe the change in words.\n"
    "2. NATIVE TOOL CALLS only — do not print the call as text, JSON, or inside ``` fences. Any "
    "hidden reasoning is stripped before the user sees it, so it changes nothing on its own; "
    "keep it short — this is simple scheduling, not a puzzle.\n"
    "3. ONE TOOL FOR BULK JOBS: plan_day (ordered day with fixed anchors + chunking), "
    "schedule_tasks (fit tasks into free time), replace_day (rebuild), shift_blocks (move the "
    "whole day). Don't chain single add_block calls.\n"
    "4. EXACT SHAPES: times = 24-hour zero-padded 'HH:MM' strings; dates = 'YYYY-MM-DD' or the "
    "user's words (never invent the year). tasks / fixed / blocks are arrays of objects.\n"
    "5. After multi-step edits, call list_blocks, fix anything in its CONFLICTS section, then "
    "confirm in ONE short sentence.\n"
)

_MISTRAL_GUIDANCE = (
    "\n\n══ MODEL-SPECIFIC INSTRUCTIONS — Mistral ══\n"
    "Your function-calling is solid — use it precisely and literally:\n"
    "1. ALWAYS call the matching tool for any schedule change; prose alone changes nothing.\n"
    "2. Use the NATIVE function-calling channel — never emit the call as text, an array, or "
    "inside ``` fences.\n"
    "3. EXACT ARGUMENT SHAPES: times = 24-hour zero-padded 'HH:MM' strings; dates = 'YYYY-MM-DD' "
    "or the user's words (never invent the year). plan_day.tasks / plan_day.fixed / "
    "schedule_tasks.tasks / replace_day.blocks are JSON arrays of objects with the required keys.\n"
    "4. ONE TOOL PER BULK JOB: plan_day / schedule_tasks / replace_day / shift_blocks / "
    "add_recurring — don't loop single add_block calls for a bulk change.\n"
    "5. After multi-step edits, call list_blocks, fix anything in its CONFLICTS section, then "
    "confirm in ONE short sentence.\n"
)

def model_guidance(model: str) -> str:
    """Extensively detailed, model-specific addendum to the system prompt, chosen by
    matching the model tag. Targets each family's known weaknesses on this tool-heavy
    scheduling task."""
    m = (model or "").lower()
    if "deepseek" in m or "r1" in m:
        return _R1_GUIDANCE
    if "gpt-oss" in m or "gpt_oss" in m or "gptoss" in m:
        return _GPTOSS_GUIDANCE
    if "qwen3" in m:
        return _QWEN3_GUIDANCE
    if "qwen2" in m or "qwen-2" in m or "qwen2.5" in m:
        return _QWEN25_GUIDANCE
    if "gemma" in m:
        return _GEMMA_GUIDANCE
    if "glm" in m:
        return _GLM_GUIDANCE
    if "mistral" in m or "mixtral" in m:
        return _MISTRAL_GUIDANCE
    return _GENERIC_GUIDANCE


def model_profile(model: str) -> Optional[Dict]:
    """User-facing profile for a model tag, or None if it isn't a curated pick.
    Exact tag match first, then family/prefix so `qwen3:14b-q4_K_M` still maps."""
    tag = (model or "").strip().split("@", 1)[0]
    if not tag:
        return None
    if tag in MODEL_PROFILES:
        return MODEL_PROFILES[tag]
    low = tag.lower()
    for key, prof in MODEL_PROFILES.items():
        if key.lower() == low:
            return prof
    # Prefix / quant suffix: curated `qwen3:14b` matches `qwen3:14b-q4_K_M`
    for key, prof in sorted(MODEL_PROFILES.items(), key=lambda kv: -len(kv[0])):
        k = key.lower()
        if low.startswith(k) and (len(low) == len(k) or low[len(k)] in ":-_"):
            return prof
    # Family match for size-tagged keys (`deepseek-r1:14b` ↔ `deepseek-r1:14b-…`)
    # and untagged keys (`gemma4` ↔ `gemma4:e4b` / `gemma4:latest`).
    for key, prof in sorted(MODEL_PROFILES.items(), key=lambda kv: -len(kv[0])):
        k = key.lower()
        k_name, _, k_size = k.partition(":")
        low_name, _, low_rest = low.partition(":")
        if low_name != k_name:
            continue
        if not k_size:
            return prof
        if low_rest == k_size or low_rest.startswith(k_size + "-") or low_rest.startswith(k_size + "_"):
            return prof
    return None


def model_when_text(model: str) -> str:
    """One short paragraph for tooltips / the Settings helper under the picker."""
    p = model_profile(model)
    if not p:
        return (
            "Custom / unlisted model. This app is tool-heavy — prefer a model with "
            "strong function-calling. Verify plan/edit requests before trusting it."
        )
    return f"{p['badge']}  ·  VRAM {p['vram']}  ·  download {p['disk']}\n{p['when']}"


def model_guide_text() -> str:
    """Full multi-model guide for the Settings / AI-panel model guide dialog."""
    lines = [
        "This app edits your schedule via tool calls, so tool-calling reliability "
        "matters more than raw size. Pull with:  ollama pull <tag>",
        "",
        "Pick by GPU VRAM (Task Manager → GPU, or nvidia-smi / rocm-smi):",
        "  • 12–16 GB  →  qwen3:14b  (recommended daily driver)",
        "  • 16 GB tight  →  mistral-small3.1:24b  (best quality; unload other apps)",
        "  • Plenty of VRAM (20 GB+)  →  glm-4.7-flash is an option (~19 GB download)",
        "  • ~8 GB or less  →  ollama pull qwen3:8b (not in the curated list; slower)",
        "",
    ]
    for tag, p in MODEL_PROFILES.items():
        lines.append(f"── {tag}  ({p['badge']})")
        lines.append(f"   VRAM {p['vram']}  ·  download {p['disk']}")
        lines.append(f"   {p['when']}")
        lines.append("")
    lines.append(
        "After pulling, pick the tag in Settings or the AI panel. Press ▶ to start "
        "Ollama; ⏏ unloads the model and ⏻ stops the server (zero GPU use until ▶)."
    )
    return "\n".join(lines)


def show_model_guide(parent=None):
    """Scrollable model guide (QMessageBox truncates long text on some platforms)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Which model should I use?")
    dlg.resize(520, 480)
    lay = QVBoxLayout(dlg)
    body = QTextEdit()
    body.setReadOnly(True)
    body.setPlainText(model_guide_text())
    body.setStyleSheet(
        f"QTextEdit {{ background: {theme.C_BG.name()}; color: {theme.C_TEXT.name()}; "
        f"border: 1px solid {theme.C_BORDER.name()}; border-radius: {theme.RAD}px; "
        f"padding: 8px; font-size: 12px; }}")
    lay.addWidget(body)
    close = QPushButton("Close")
    close.setStyleSheet(
        f"QPushButton {{ background:{theme.C_ACCENT.name()}; color:{theme.C_ON_ACCENT.name()}; "
        f"border:none; padding:7px 18px; border-radius:{theme.RAD}px; font-weight:bold; }}")
    close.clicked.connect(dlg.accept)
    row = QHBoxLayout(); row.addStretch(); row.addWidget(close)
    lay.addLayout(row)
    dlg.exec()
