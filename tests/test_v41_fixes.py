"""v4.1.0 — code-review fixes + Ctrl+Y redo. Offscreen, synthetic data only
(DATA_FILE/etc. redirected to a temp dir — never the real store). Covers:
model_is_installed exact-match (no prefix false-positives), the installed-models
cache (no GUI-thread HTTP from hint/keystroke paths), OllamaCheckThread's models
signal, CalFetchThread per-calendar fault isolation, the unified Ctrl+Z/Ctrl+Y
undo-redo history (AI turns included, no silent AI-work loss), and the debounced
splitter-layout save."""
import os, sys, json, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import datetime
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

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)

# ── model_is_installed: EXACT match only ──────────────────────────────────────
check("exact tag matches", app.model_is_installed("qwen3:14b", ["qwen3:14b"]))
check(":latest normalizes", app.model_is_installed("gemma4", ["gemma4:latest"]))
check("bare name does NOT match a specific tag (Ollama would 404 on :latest)",
      not app.model_is_installed("deepseek-r1", ["deepseek-r1:14b"]))
check("quant-suffix install does NOT satisfy the curated tag",
      not app.model_is_installed("qwen3:14b", ["qwen3:14b-q4_K_M"]))

# ── installed-models cache: no GUI-thread HTTP after construction ─────────────
calls = {"n": 0}
_real_list = app.list_ollama_models
app.list_ollama_models = lambda: (calls.__setitem__("n", calls["n"] + 1) or ["qwen3:14b"])
try:
    panel = app.AIPanel(lambda: {})
    seed = calls["n"]
    check("construction seeds the cache (bounded, one-off)", seed >= 1)
    panel._on_ollama(True)             # 30-s poll path
    panel._on_model_changed("mistral-small3.1:24b")   # keystroke path
    panel._refresh_model_hint()
    check("hint/keystroke/poll paths do ZERO further HTTP calls", calls["n"] == seed)
    panel._on_models(["qwen3:14b", "gemma4:latest"])  # poll thread delivers update
    check("poll-delivered list updates the cache",
          panel._installed_models == ["qwen3:14b", "gemma4:latest"])
    check("choices read the cache", "gemma4:latest" in panel._model_choices())
finally:
    app.list_ollama_models = _real_list

# ── OllamaCheckThread parses tags from the same /api/tags response ────────────
class _FakeResp:
    ok = True
    def json(self):
        return {"models": [{"name": "qwen3:14b"}, {"name": ""}, {"name": "gemma4:latest"}]}
_real_get = app.requests.get
app.requests.get = lambda *a, **k: _FakeResp()
try:
    got = {}
    t = app.OllamaCheckThread()
    t.models.connect(lambda tags: got.__setitem__("tags", tags))
    t.result.connect(lambda ok: got.__setitem__("ok", ok))
    t.run()   # synchronous: run() directly, no thread start
    check("check thread emits ok", got.get("ok") is True)
    check("check thread emits non-empty tags only",
          got.get("tags") == ["qwen3:14b", "gemma4:latest"])
finally:
    app.requests.get = _real_get

# ── CalFetchThread: one bad calendar can't blank the good ones ────────────────
def _ev(hour):
    s = datetime(2026, 7, 13, hour, 0).astimezone()
    e = datetime(2026, 7, 13, hour + 1, 0).astimezone()
    return {"id": f"g{hour}", "summary": f"E{hour}",
            "start": {"dateTime": s.isoformat()}, "end": {"dateTime": e.isoformat()}}
DAY = datetime(2026, 7, 13, 10, 0).astimezone().date().isoformat()

class _FakeSvc:
    class _Events:
        def list(self, calendarId=None, **kw):
            class _Req:
                def __init__(self, cid): self.cid = cid
                def execute(self):
                    if self.cid == "typo@bad":
                        raise RuntimeError("404 calendar not found")
                    return {"items": [_ev(10)], "nextPageToken": None}
            return _Req(calendarId)
    def events(self): return self._Events()

ct = app.CalFetchThread(None, datetime(2026, 7, 13).date(), datetime(2026, 7, 14).date(),
                        calendar_ids=["primary", "typo@bad"])
by_date, failed = ct._collect(_FakeSvc())
check("good calendar's events survive a bad one", DAY in by_date and len(by_date[DAY]) == 1)
check("failure names the bad calendar only", failed == ["typo@bad"])
check("event ids are namespaced by calendar", by_date[DAY][0]["id"].startswith("primary:"))
ct2 = app.CalFetchThread(None, datetime(2026, 7, 13).date(), datetime(2026, 7, 14).date(),
                         calendar_ids=["typo@bad"])
bd2, f2 = ct2._collect(_FakeSvc())
check("all-failed reports every id (run() then emits error)", bd2 == {} and f2 == ["typo@bad"])

# ── Unified undo/redo history (Ctrl+Z / Ctrl+Y) ───────────────────────────────
def blk(i):
    return {"id": f"a{i}", "date": "2026-07-13", "startMin": 600 + i * 60,
            "endMin": 660 + i * 60, "title": f"B{i}", "type": "study", "color": "#888"}

class FakePanel:
    enabled = None
    def set_undo_enabled(self, on): self.enabled = on

class Fake: pass
s = Fake()
s._ai_undo = []; s._manual_undo = []; s._manual_redo = []
s._ai_turn_snapshotted = False; s._ai_turn_active = False
s._all_acts = [blk(1)]
s._ai_panel = FakePanel()
s._refresh_view = lambda: None
s._set_status = lambda m: None
for m in ("_manual_snapshot", "_manual_undo_last", "_manual_redo_last",
          "_ai_snapshot_before", "_ai_turn_start", "_ai_turn_end",
          "_ai_undo_last", "_ai_undo_invalidate", "_update_undo_state"):
    setattr(s, m, getattr(app.MainWindow, m).__get__(s))

# Manual edit, then an AI turn — Ctrl+Z must undo ONLY the AI turn.
s._manual_snapshot(); s._all_acts = s._all_acts + [blk(2)]      # manual edit
s._ai_turn_start(); s._ai_snapshot_before("add_block")
s._all_acts = s._all_acts + [blk(3), blk(4)]                    # the AI's "plan"
s._ai_turn_end()
check("AI turn feeds the Ctrl+Z history too", len(s._manual_undo) == 2)
s._manual_undo_last()                                            # Ctrl+Z
check("Ctrl+Z undoes just the AI turn (manual edit intact)",
      [a["id"] for a in s._all_acts] == ["a1", "a2"])
check("undone AI plan is on the redo stack", len(s._manual_redo) == 1)
s._manual_redo_last()                                            # Ctrl+Y
check("Ctrl+Y restores the AI plan", [a["id"] for a in s._all_acts] == ["a1", "a2", "a3", "a4"])
check("Ctrl+Y round-trips the undo stack", len(s._manual_undo) == 2 and not s._manual_redo)
s._manual_undo_last()
check("undo after redo works", [a["id"] for a in s._all_acts] == ["a1", "a2"])
s._manual_snapshot(); s._all_acts = s._all_acts + [blk(5)]       # NEW edit forks history
check("new edit clears the redo stack", s._manual_redo == [])
s._ai_turn_active = True
before = [a["id"] for a in s._all_acts]
s._manual_undo_last(); s._manual_redo_last()
check("undo/redo locked while an AI turn streams",
      [a["id"] for a in s._all_acts] == before)
s._ai_turn_active = False

# AI ↶ Undo: feeds redo, pops its Ctrl+Z duplicate, Ctrl+Y can redo it.
s._ai_turn_start(); s._ai_snapshot_before("add_block")
s._all_acts = s._all_acts + [blk(6)]
s._ai_turn_end()
mu = len(s._manual_undo)
s._ai_undo_last()                                                # ↶ button
check("AI-undo pops its Ctrl+Z duplicate", len(s._manual_undo) == mu - 1)
check("AI-undo feeds redo (Ctrl+Y available)", len(s._manual_redo) == 1)
s._manual_redo_last()
check("Ctrl+Y redoes an AI-undo", s._all_acts[-1]["id"] == "a6")

# A do-nothing AI turn leaves no stale undo point on either stack.
mu, au = len(s._manual_undo), len(s._ai_undo)
s._ai_turn_start(); s._ai_snapshot_before("add_block"); s._ai_turn_end()  # no change
check("do-nothing turn drops both snapshots",
      len(s._manual_undo) == mu and len(s._ai_undo) == au)

# ── Debounced splitter save ───────────────────────────────────────────────────
mw = app.MainWindow()
app.SETTINGS_FILE.unlink(missing_ok=True)
mw._persist_layout_splits()
check("splitter save is debounced (no immediate write)",
      mw._split_save_timer.isActive() and not app.SETTINGS_FILE.exists())
mw._persist_layout_splits_now()
check("flush writes settings", app.SETTINGS_FILE.exists()
      and isinstance(json.loads(app.SETTINGS_FILE.read_text()).get("body_split"), list))

n = len(results)
print(f"RESULT: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{n})")
sys.exit(0 if all(results) else 1)
