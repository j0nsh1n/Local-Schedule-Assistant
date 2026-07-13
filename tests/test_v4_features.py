"""v4.0.0 — model install checks, calendar ID parse, backup listing helpers.
Synthetic temp DATA_DIR only — no real schedule data."""
import os, sys, tempfile, json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app

TMP = Path(tempfile.mkdtemp())
app.DATA_DIR = TMP
app.DATA_FILE = TMP / "activities.json"
app.BAK_FILE = TMP / "activities.json.bak"
app.BACKUP_DIR = TMP / "backups"
app.SETTINGS_FILE = TMP / "settings.json"
app.CHAT_FILE = TMP / "chat.json"
app.CREDS_FILE = TMP / "c.json"
app.TOKEN_FILE = TMP / "t.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

check("version 4.0.0", app.APP_VERSION == "4.0.0")

# model_is_installed
check("exact match", app.model_is_installed("qwen3:14b", ["qwen3:14b"]))
check(":latest strip", app.model_is_installed("qwen3:14b", ["qwen3:14b:latest"]))
check("missing", not app.model_is_installed("gemma4", ["qwen3:14b"]))
check("empty", not app.model_is_installed("", ["qwen3:14b"]))
check("tag key normalizes", app._model_tag_key("Foo:Bar:latest") == "foo:bar")

# parse_calendar_ids
check("default primary", app.parse_calendar_ids("") == ["primary"])
check("split commas", app.parse_calendar_ids("primary, school@x.com") ==
      ["primary", "school@x.com"])
check("whitespace only → primary", app.parse_calendar_ids("  ,  ") == ["primary"])

# backups listing
app.DATA_FILE.write_text("[]", encoding="utf-8")
app.save_all_activities([{"id": "a", "date": "2026-07-14", "startMin": 600,
                          "endMin": 660, "type": "study", "color": "#fff",
                          "title": "Test"}])
items = app.list_schedule_backups()
check("has bak and/or daily after save", len(items) >= 1)
loaded = app.load_activities_from_path(items[0]["path"])
check("load backup returns list", isinstance(loaded, list) and len(loaded) >= 1)
check("load garbage → None",
      app.load_activities_from_path(TMP / "nope.json") is None)

# settings defaults include new keys
s = app.load_settings()
check("notify_end_chime default", s.get("notify_end_chime") is True)
check("calendar_ids default", s.get("calendar_ids") == "primary")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
