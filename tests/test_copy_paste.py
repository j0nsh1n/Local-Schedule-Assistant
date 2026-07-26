"""Block copy / paste / duplicate (Ctrl+C V D + right-click). Synthetic data only."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import mainwindow
from PySide6.QtWidgets import QApplication

TMP = Path(tempfile.mkdtemp())
core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.CREDS_FILE    = TMP / "credentials.json"
core.TOKEN_FILE    = TMP / "token.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)

def blk(i, ds, sm=600, dur=60):
    return {"id": f"b{i}", "date": ds, "startMin": sm, "endMin": sm + dur,
            "title": f"Task{i}", "type": "study", "color": "#8b5cf6"}

DAY = date(2026, 7, 28)
DS = DAY.isoformat()

mw = mainwindow.MainWindow()
mw._cur_date = DAY
mw._all_acts = [blk(1, DS, 600), blk(2, DS, 720)]
mw._selected_aid = None
mw._clip_act = None

print("── selection + copy ──")
mw._copy_activity(None)
check("copy with no selection is a no-op", mw._clip_act is None)
mw._select_activity("b1")
mw._copy_activity("b1")
check("clipboard set", mw._clip_act is not None)
check("clipboard has no id", "id" not in mw._clip_act)
check("clipboard keeps title/time",
      mw._clip_act.get("title") == "Task1" and mw._clip_act.get("startMin") == 600)

print("── duplicate ──")
n0 = len(mw._all_acts)
mw._duplicate_activity("b1")
check("duplicate adds one block", len(mw._all_acts) == n0 + 1)
dup = next(a for a in mw._all_acts if a["id"] not in ("b1", "b2"))
check("duplicate new id", dup["id"] not in ("b1", "b2"))
check("duplicate same day/time",
      dup["date"] == DS and dup["startMin"] == 600 and dup["endMin"] == 660)
check("duplicate same title", dup["title"] == "Task1")
check("selection moves to clone", mw._selected_aid == dup["id"])

print("── paste onto viewed day ──")
mw._cur_date = date(2026, 7, 29)
n1 = len(mw._all_acts)
mw._paste_activity()
check("paste adds one", len(mw._all_acts) == n1 + 1)
pasted = [a for a in mw._all_acts if a["date"] == "2026-07-29"]
check("paste lands on viewed day", len(pasted) == 1)
check("paste keeps times", pasted[0]["startMin"] == 600 and pasted[0]["endMin"] == 660)
check("paste title preserved", pasted[0]["title"] == "Task1")

print("── paste empty clipboard ──")
mw._clip_act = None
n2 = len(mw._all_acts)
mw._paste_activity()
check("empty paste is no-op", len(mw._all_acts) == n2)

print("── timeline signals exist ──")
check("timeline has copy signal", hasattr(mw._timeline, "activity_copy_req"))
check("timeline has dup signal", hasattr(mw._timeline, "activity_dup_req"))
check("week has paste signal", hasattr(mw._week_view, "activity_paste_req"))

print("── text-field guard ──")
check("no focus → not text field", mw._text_field_focused() is False)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
