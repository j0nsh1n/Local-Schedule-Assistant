"""End-of-day 24:00 / QTime 00:00 convention (landed in v3.9.1).
QTime only holds 00:00–23:59; blocks that run to midnight use endMin=1440."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTime

TMP = Path(tempfile.mkdtemp())
app.DATA_FILE = TMP / "a.json"
app.SETTINGS_FILE = TMP / "s.json"
app.CREDS_FILE = TMP / "c.json"
app.TOKEN_FILE = TMP / "t.json"
app.CHAT_FILE = TMP / "chat.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)
app.apply_theme("nocturne")

check("24:00 → DAY_END", app.parse_hhmm("24:00") == app.DAY_END == 1440)
check("2400 → DAY_END", app.parse_hhmm("2400") == 1440)
check("24 → DAY_END", app.parse_hhmm("24") == 1440)
check("23:59 still 1439", app.parse_hhmm("23:59") == 1439)
check("00:00 still 0", app.parse_hhmm("00:00") == 0)
check("fmt 1440 is 24:00", app.fmt_time(1440) == "24:00")

check("00:00 end after start → eod", app.coerce_end_min(22 * 60, 0) == 1440)
check("00:00 end with start 0 stays 0", app.coerce_end_min(0, 0) == 0)
check("normal end unchanged", app.coerce_end_min(600, 720) == 720)

dlg = app.AddActivityDialog(
    22 * 60, 1440, "sleep",
    existing={"id": "x1", "date": "2026-07-14", "startMin": 22 * 60, "endMin": 1440,
              "type": "sleep", "color": "#888", "title": "Sleep"},
    for_date="2026-07-14",
)
check("dialog shows end as 00:00 for eod", dlg.t_end.time() == QTime(0, 0))
check("dialog start 22:00", dlg.t_start.time() == QTime(22, 0))
dlg._save()
check("save maps 00:00 end → 1440",
      dlg.result_activity is not None and dlg.result_activity["endMin"] == 1440)
check("save keeps start 22:00", dlg.result_activity["startMin"] == 22 * 60)

dlg2 = app.AddActivityDialog(10 * 60, 11 * 60, "study", for_date="2026-07-14")
dlg2.t_start.setTime(QTime(10, 0))
dlg2.t_end.setTime(QTime(0, 0))
dlg2._save()
check("10:00–00:00 saves as end 1440",
      dlg2.result_activity and dlg2.result_activity["endMin"] == 1440)
check("duration is 14h",
      dlg2.result_activity["endMin"] - dlg2.result_activity["startMin"] == 14 * 60)

dlg3 = app.AddActivityDialog(0, 60, "study", for_date="2026-07-14")
dlg3.t_start.setTime(QTime(0, 0))
dlg3.t_end.setTime(QTime(0, 0))
with patch.object(app.QMessageBox, "warning", return_value=app.QMessageBox.Ok):
    dlg3._save()
check("00:00–00:00 rejected", dlg3.result_activity is None)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
