"""v3.8.0 — crash resilience: chat transcript persist + soft memory preflight
+ friendly mid-stream error text. Pure helpers + light AIPanel wiring (offscreen)."""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
from PySide6.QtWidgets import QApplication

TMP = Path(tempfile.mkdtemp())
app.DATA_FILE     = TMP / "activities.json"
app.BACKUP_DIR    = TMP / "backups"
app.SETTINGS_FILE = TMP / "settings.json"
app.CREDS_FILE    = TMP / "credentials.json"
app.TOKEN_FILE    = TMP / "token.json"
app.CHAT_FILE     = TMP / "chat.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)
app.apply_theme("nocturne")

# ── model_need_gb / memory_warning_for ───────────────────────────────────────
check("qwen3 need ~10", app.model_need_gb("qwen3:14b") == 10.0)
check("mistral need ~15", app.model_need_gb("mistral-small3.1:24b") == 15.0)
check("range takes high end", app.model_need_gb("gpt-oss:20b") in (13.0, 14.0))
check("unknown model need None", app.model_need_gb("totally-fake:1b") is None)

real_free = app.free_ram_gb
try:
    app.free_ram_gb = lambda: 4.0
    w = app.memory_warning_for("mistral-small3.1:24b")
    check("warns when free << need", "4 GB" in w and "mistral" in w.lower())
    check("warn mentions VRAM caveat", "VRAM" in w or "GPU" in w)

    app.free_ram_gb = lambda: 40.0
    check("no warn when free is ample", app.memory_warning_for("mistral-small3.1:24b") == "")

    app.free_ram_gb = lambda: None
    check("no warn when free unknown", app.memory_warning_for("qwen3:14b") == "")
finally:
    app.free_ram_gb = real_free

# ── friendly_stream_error ────────────────────────────────────────────────────
ex = ConnectionError("Connection reset by peer")
msg = app.friendly_stream_error(ex, got_tokens=True, model="qwen3:14b")
check("mid-stream mentions cut off", "cut off" in msg.lower() or "mid-stream" in msg.lower())
check("mid-stream mentions OOM", "memory" in msg.lower() or "OOM" in msg)
check("mid-stream names model", "qwen3:14b" in msg)

cold = app.friendly_stream_error(ConnectionError("refused"), got_tokens=False, model="x")
# refused still connection-like → oomish path may apply; either is ok as long as helpful
check("cold connection is helpful", "Ollama" in cold or "▶" in cold or "memory" in cold.lower())

# ── save / load transcript ───────────────────────────────────────────────────
app._chat_save_last = 0.0
hist = {
    "chat": [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "plan my day"},
        {"role": "assistant", "content": "partial reply…"},
    ],
    "plan": [{"role": "user", "content": "essay"}],
    "suggest": [],
}
check("force save writes file", app.save_chat_histories(hist, force=True))
check("chat.json exists", app.CHAT_FILE.exists())

loaded = app.load_chat_histories()
check("restored chat user msg", any(m.get("content") == "plan my day" for m in loaded["chat"]))
check("restored partial assistant", any("partial" in m.get("content", "") for m in loaded["chat"]))
check("restored plan mode", any(m.get("content") == "essay" for m in loaded["plan"]))

# throttle: immediate non-force save after force should often no-op
app._chat_save_last = time.monotonic()
hist2 = {"chat": [{"role": "user", "content": "SHOULD_NOT_APPEAR_YET"}],
         "plan": [], "suggest": []}
check("throttled save returns False", app.save_chat_histories(hist2, force=False) is False)
loaded2 = app.load_chat_histories()
check("throttled save did not overwrite",
      not any(m.get("content") == "SHOULD_NOT_APPEAR_YET" for m in loaded2["chat"]))

check("force bypasses throttle", app.save_chat_histories(hist2, force=True))
loaded3 = app.load_chat_histories()
check("force save did overwrite",
      any(m.get("content") == "SHOULD_NOT_APPEAR_YET" for m in loaded3["chat"]))

# corrupt file → defaults
app.CHAT_FILE.write_text("{not json", encoding="utf-8")
fallback = app.load_chat_histories()
check("corrupt → default chat greeting present",
      any(m.get("role") == "assistant" for m in fallback["chat"]))
check("corrupt → plan empty", fallback["plan"] == [])

# drop non-roles / garbage entries
app.save_chat_histories({
    "chat": [
        {"role": "user", "content": "ok"},
        {"role": "system", "content": "nope"},
        {"role": "user", "content": 123},
        "junk",
    ],
    "plan": [], "suggest": [],
}, force=True)
cleaned = app.load_chat_histories()
check("only allowed roles restored",
      all(m["role"] in ("user", "assistant", "tool_note", "error") for m in cleaned["chat"]))
check("non-str content dropped", all(isinstance(m["content"], str) for m in cleaned["chat"]))

# ── AIPanel wires load + once-per-model warning ──────────────────────────────
app.save_chat_histories({
    "chat": [{"role": "user", "content": "restored-user"},
             {"role": "assistant", "content": "restored-asst"}],
    "plan": [], "suggest": [],
}, force=True)
# silence network for status poll
real_get = app.requests.get
app.requests.get = lambda *a, **k: type("R", (), {"ok": False})()
try:
    real_free2 = app.free_ram_gb
    app.free_ram_gb = lambda: 2.0
    panel = app.AIPanel(lambda: {})
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
    app.free_ram_gb = real_free2
finally:
    app.requests.get = real_get

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
