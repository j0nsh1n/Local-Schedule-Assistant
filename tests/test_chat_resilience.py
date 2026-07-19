"""v3.8.0 — crash resilience: chat transcript persist + soft memory preflight
+ friendly mid-stream error text. Pure helpers + light AIPanel wiring (offscreen)."""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai
import aipanel
import core
import theme
import requests
from PySide6.QtWidgets import QApplication

TMP = Path(tempfile.mkdtemp())
core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.CREDS_FILE    = TMP / "credentials.json"
core.TOKEN_FILE    = TMP / "token.json"
core.CHAT_FILE     = TMP / "chat.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)
theme.apply_theme("nocturne")

# ── model_need_gb / memory_warning_for ───────────────────────────────────────
check("qwen3 need ~10", ai.model_need_gb("qwen3:14b") == 10.0)
check("mistral need ~15", ai.model_need_gb("mistral-small3.1:24b") == 15.0)
check("range takes high end", ai.model_need_gb("gpt-oss:20b") in (13.0, 14.0))
check("unknown model need None", ai.model_need_gb("totally-fake:1b") is None)

real_free = ai.free_ram_gb
try:
    ai.free_ram_gb = lambda: 4.0
    w = ai.memory_warning_for("mistral-small3.1:24b")
    check("warns when free << need", "4 GB" in w and "mistral" in w.lower())
    check("warn mentions VRAM caveat", "VRAM" in w or "GPU" in w)

    ai.free_ram_gb = lambda: 40.0
    check("no warn when free is ample", ai.memory_warning_for("mistral-small3.1:24b") == "")

    ai.free_ram_gb = lambda: None
    check("no warn when free unknown", ai.memory_warning_for("qwen3:14b") == "")
finally:
    ai.free_ram_gb = real_free

# ── friendly_stream_error ────────────────────────────────────────────────────
ex = ConnectionError("Connection reset by peer")
msg = ai.friendly_stream_error(ex, got_tokens=True, model="qwen3:14b")
check("mid-stream mentions cut off", "cut off" in msg.lower() or "mid-stream" in msg.lower())
check("mid-stream mentions OOM", "memory" in msg.lower() or "OOM" in msg)
check("mid-stream names model", "qwen3:14b" in msg)

cold = ai.friendly_stream_error(ConnectionError("refused"), got_tokens=False, model="x")
# refused still connection-like → oomish path may apply; either is ok as long as helpful
check("cold connection is helpful", "Ollama" in cold or "▶" in cold or "memory" in cold.lower())

# ── save / load transcript ───────────────────────────────────────────────────
ai._chat_save_last = 0.0
hist = {
    "chat": [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "plan my day"},
        {"role": "assistant", "content": "partial reply…"},
    ],
    "plan": [{"role": "user", "content": "essay"}],
    "suggest": [],
}
check("force save writes file", ai.save_chat_histories(hist, force=True))
check("chat.json exists", core.CHAT_FILE.exists())

loaded = ai.load_chat_histories()
check("restored chat user msg", any(m.get("content") == "plan my day" for m in loaded["chat"]))
check("restored partial assistant", any("partial" in m.get("content", "") for m in loaded["chat"]))
check("restored plan mode", any(m.get("content") == "essay" for m in loaded["plan"]))

# throttle: immediate non-force save after force should often no-op
ai._chat_save_last = time.monotonic()
hist2 = {"chat": [{"role": "user", "content": "SHOULD_NOT_APPEAR_YET"}],
         "plan": [], "suggest": []}
check("throttled save returns False", ai.save_chat_histories(hist2, force=False) is False)
loaded2 = ai.load_chat_histories()
check("throttled save did not overwrite",
      not any(m.get("content") == "SHOULD_NOT_APPEAR_YET" for m in loaded2["chat"]))

check("force bypasses throttle", ai.save_chat_histories(hist2, force=True))
loaded3 = ai.load_chat_histories()
check("force save did overwrite",
      any(m.get("content") == "SHOULD_NOT_APPEAR_YET" for m in loaded3["chat"]))

# corrupt file → defaults
core.CHAT_FILE.write_text("{not json", encoding="utf-8")
fallback = ai.load_chat_histories()
check("corrupt → default chat greeting present",
      any(m.get("role") == "assistant" for m in fallback["chat"]))
check("corrupt → plan empty", fallback["plan"] == [])

# drop non-roles / garbage entries
ai.save_chat_histories({
    "chat": [
        {"role": "user", "content": "ok"},
        {"role": "system", "content": "nope"},
        {"role": "user", "content": 123},
        "junk",
    ],
    "plan": [], "suggest": [],
}, force=True)
cleaned = ai.load_chat_histories()
check("only allowed roles restored",
      all(m["role"] in ("user", "assistant", "tool_note", "error") for m in cleaned["chat"]))
check("non-str content dropped", all(isinstance(m["content"], str) for m in cleaned["chat"]))

# ── AIPanel wires load + once-per-model warning ──────────────────────────────
ai.save_chat_histories({
    "chat": [{"role": "user", "content": "restored-user"},
             {"role": "assistant", "content": "restored-asst"}],
    "plan": [], "suggest": [],
}, force=True)
# silence network for status poll
real_get = requests.get
requests.get = lambda *a, **k: type("R", (), {"ok": False})()
try:
    real_free2 = ai.free_ram_gb
    ai.free_ram_gb = lambda: 2.0
    panel = aipanel.AIPanel(lambda: {})
    panel._timer.stop()
    check("panel restores transcript",
          any(m.get("content") == "restored-user" for m in panel.history["chat"]))

    # force a model with high need
    panel.model = "mistral-small3.1:24b"
    panel._mem_warned.clear()
    before = len(panel.history[panel.mode])
    panel._maybe_memory_warning()
    check("warning injected once", len(panel.history[panel.mode]) == before + 1)
    panel._maybe_memory_warning()
    check("warning not repeated", len(panel.history[panel.mode]) == before + 1)
    ai.free_ram_gb = real_free2
finally:
    requests.get = real_get

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
