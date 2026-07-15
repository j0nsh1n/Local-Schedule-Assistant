"""v2.6.0 — auto-backups + AI undo. Offscreen, synthetic data only (never the real
store: DATA_FILE/BACKUP_DIR are redirected to a temp dir). Covers the review fixes:
undo locked during a streaming turn, do-nothing turns dropping their snapshot, and
manual edits invalidating the stack."""
import os, sys, json, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app

TMP = Path(tempfile.mkdtemp())
app.DATA_FILE  = TMP / "activities.json"
app.BACKUP_DIR = TMP / "backups"

results = []
def check(name, cond):
    results.append(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

def _blk(i, sm):
    return {"id": f"a{i}", "date": "2026-07-04", "startMin": sm, "endMin": sm + 60,
            "title": f"B{i}", "type": "study", "color": "#888"}

# ── Backups ──────────────────────────────────────────────────────────────────
acts = [_blk(1, 600)]
app.save_all_activities(acts)
today_bak = app.BACKUP_DIR / f"activities-{date.today().isoformat()}.json"
prev_bak  = app.DATA_FILE.with_name("activities.json.bak")
check("data file written", app.DATA_FILE.exists())
check("daily backup created", today_bak.exists())
check("backup content matches", json.loads(today_bak.read_text()) == acts)
check("no .bak on first-ever save", not prev_bak.exists())

# .bak lags the live file by exactly one save (v2.6.1: recovers a bad save even
# after a restart wiped the in-memory AI undo).
acts2 = acts + [_blk(9, 900)]
app.save_all_activities(acts2)
check(".bak holds the previous state", json.loads(prev_bak.read_text()) == acts)
check("live file holds the new state", json.loads(app.DATA_FILE.read_text()) == acts2)
app.save_all_activities(acts)                       # save again ("the bad save")
check(".bak rotated to one-save-ago", json.loads(prev_bak.read_text()) == acts2)

# Pruning: seed KEEP+5 old dated backups, save again, expect only newest KEEP remain.
for i in range(app.BACKUP_KEEP + 5):
    (app.BACKUP_DIR / f"activities-2026-01-{i + 1:02d}.json").write_text("[]")
app.save_all_activities(acts)
remaining = sorted(app.BACKUP_DIR.glob("activities-*.json"))
check(f"pruned to {app.BACKUP_KEEP} (got {len(remaining)})", len(remaining) == app.BACKUP_KEEP)
check("today's backup survives prune", today_bak in remaining)

# ── AI undo ──────────────────────────────────────────────────────────────────
class FakePanel:
    enabled = None
    def set_undo_enabled(self, on): self.enabled = on

class Fake: pass
s = Fake()
s._ai_undo = []
s._manual_undo = []   # v4.1: AI snapshots also feed the Ctrl+Z history
s._manual_redo = []
s._ai_turn_snapshotted = False
s._ai_turn_active = False
s._all_acts = [_blk(1, 600)]
s._ai_panel = FakePanel()
s._refresh_view = lambda: None
s._set_status = lambda m: None
for m in ("_ai_turn_start", "_ai_turn_end", "_ai_snapshot_before", "_ai_undo_last",
          "_ai_undo_invalidate", "_update_undo_state", "_manual_snapshot"):
    setattr(s, m, getattr(app.MainWindow, m).__get__(s))

# Turn 1: first mutating tool snapshots; Undo stays LOCKED until the turn ends.
s._ai_turn_start()
check("undo locked at turn start", s._ai_panel.enabled is False)
s._ai_snapshot_before("add_block")
check("snapshot taken on first mutation", len(s._ai_undo) == 1)
check("undo still locked mid-turn", s._ai_panel.enabled is False)
s._all_acts.append(_blk(2, 700))                       # the "change"
s._ai_snapshot_before("move_block")                    # 2nd tool, same turn
check("one snapshot per turn (not per tool)", len(s._ai_undo) == 1)
s._ai_undo_last()                                      # user mashes Undo mid-turn
check("mid-turn undo refused (state intact)",
      len(s._all_acts) == 2 and len(s._ai_undo) == 1)
s._ai_turn_end()
check("undo unlocked after turn", s._ai_panel.enabled is True)

# A read-only turn never snapshots.
s._ai_turn_start(); s._ai_snapshot_before("week_summary"); s._ai_turn_end()
check("read-only tool does not snapshot", len(s._ai_undo) == 1)

# A do-nothing turn (tool failed / no-op) drops its snapshot at turn end.
s._ai_turn_start(); s._ai_snapshot_before("add_block"); s._ai_turn_end()
check("do-nothing turn drops its undo point", len(s._ai_undo) == 1)

# Undo restores the pre-turn schedule.
s._ai_undo_last()
check("undo restored the schedule", len(s._all_acts) == 1 and s._all_acts[0]["id"] == "a1")
check("undo persisted to disk", json.loads(app.DATA_FILE.read_text())[0]["id"] == "a1")
check("stack empty after undo", len(s._ai_undo) == 0)
check("undo button disabled when empty", s._ai_panel.enabled is False)
s._ai_undo_last()                                      # no-op on empty stack
check("undo is a safe no-op when empty", len(s._all_acts) == 1)

# A manual edit invalidates the stack (undo would wipe the user's own work).
s._ai_turn_start(); s._ai_snapshot_before("add_block")
s._all_acts.append(_blk(3, 800)); s._ai_turn_end()
check("real turn leaves an undo point", len(s._ai_undo) == 1)
s._ai_undo_invalidate()
check("manual edit clears the stack",
      len(s._ai_undo) == 0 and s._ai_panel.enabled is False)

# All four manual-mutation paths call the invalidator (source-level guard).
src = Path(app.__file__).read_text(encoding="utf-8")
check("all manual edit paths invalidate undo",
      src.count("self._ai_undo_invalidate()") >= 4)

print("RESULT:", "PASS" if all(results) else "FAIL")
sys.exit(0 if all(results) else 1)
