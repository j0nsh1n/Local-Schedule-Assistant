"""v4.0.0 — model install checks, calendar ID parse, backup listing helpers.
Synthetic temp DATA_DIR only — no real schedule data."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai
import core
import platform_utils

TMP = Path(tempfile.mkdtemp())
core.DATA_DIR = TMP
core.DATA_FILE = TMP / "activities.json"
core.BAK_FILE = TMP / "activities.json.bak"
core.BACKUP_DIR = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.CHAT_FILE = TMP / "chat.json"
core.CREDS_FILE = TMP / "c.json"
core.TOKEN_FILE = TMP / "t.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

check("version 4.6.2", core.APP_VERSION == "4.6.2")

# model_is_installed
check("exact match", ai.model_is_installed("qwen3:14b", ["qwen3:14b"]))
check(":latest strip", ai.model_is_installed("qwen3:14b", ["qwen3:14b:latest"]))
check("missing", not ai.model_is_installed("gemma4", ["qwen3:14b"]))
check("empty", not ai.model_is_installed("", ["qwen3:14b"]))
check("tag key normalizes", ai._model_tag_key("Foo:Bar:latest") == "foo:bar")

# parse_calendar_ids
check("default primary", core.parse_calendar_ids("") == ["primary"])
check("split commas", core.parse_calendar_ids("primary, school@x.com") ==
      ["primary", "school@x.com"])
check("whitespace only → primary", core.parse_calendar_ids("  ,  ") == ["primary"])

# backups listing
core.DATA_FILE.write_text("[]", encoding="utf-8")
core.save_all_activities([{"id": "a", "date": "2026-07-14", "startMin": 600,
                          "endMin": 660, "type": "study", "color": "#fff",
                          "title": "Test"}])
items = core.list_schedule_backups()
check("has bak and/or daily after save", len(items) >= 1)
# Don't assume items[0]: on coarse-mtime filesystems .bak (empty seed) can tie
# the daily snapshot and win the stable sort. Pick any entry with real data.
loaded_any = None
for it in items:
    data = core.load_activities_from_path(it["path"])
    if isinstance(data, list) and len(data) >= 1:
        loaded_any = data
        break
check("load backup returns list", loaded_any is not None and len(loaded_any) >= 1)
check("load garbage → None",
      core.load_activities_from_path(TMP / "nope.json") is None)

# settings defaults include new keys
s = core.load_settings()
check("notify_end_chime default off", s.get("notify_end_chime") is False)
check("notify_sound default on", s.get("notify_sound") is True)
check("notify_tone default chime", s.get("notify_tone") == "chime")
check("ollama_models_dir empty default", s.get("ollama_models_dir") == "")
check("calendar_ids default", s.get("calendar_ids") == "primary")

# tone synthesis + env helper
wav = platform_utils.ensure_alert_wav("soft")
check("synth soft tone wav", wav is not None and wav.exists() and wav.stat().st_size > 64)
env = ai.ollama_env({"ollama_models_dir": str(TMP / "ollama-models")})
check("OLLAMA_MODELS in env when set",
      env.get("OLLAMA_MODELS") == str(TMP / "ollama-models"))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
