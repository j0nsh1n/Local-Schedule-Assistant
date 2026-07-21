"""v3.7.1 — OllamaCheckThread lifecycle (crash fix).

_poll_ollama used to spawn an unreferenced QThread every 30 s; GC could destroy
it mid-request (segfault). Mirror MainWindow._update_thread: hold ref, skip if
running, deleteLater + clear on finished."""
import os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aipanel
import core
import theme
import requests
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread

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
theme.apply_theme("nocturne")

# Slow the HTTP check so we can observe "already running" behaviour.
real_get = requests.get
def slow_get(url, **kw):
    time.sleep(0.15)
    class R:
        ok = True
        status_code = 200
    return R()
requests.get = slow_get

def wait_idle(panel, seconds=5.0):
    """Drain until no check thread is running and the ref is cleared."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        t = panel._check_thread
        if t is None or not t.isRunning():
            for _ in range(20):
                qapp.processEvents()
                if panel._check_thread is None:
                    return True
                time.sleep(0.01)
        qapp.processEvents()
        time.sleep(0.02)
    return panel._check_thread is None

try:
    panel = aipanel.AIPanel(lambda: {})
    # Stop the 30 s timer so it doesn't fire extra polls during the test.
    panel._timer.stop()
    # __init__ already kicked one poll — wait it out so we start clean.
    check("init poll settles (ref cleared)", wait_idle(panel))
    check("idle → no check thread", panel._check_thread is None)

    panel._poll_ollama()
    first = panel._check_thread
    check("holds a thread ref after poll", first is not None)
    check("thread is a QThread", isinstance(first, QThread))
    check("thread is running after poll", first is not None and first.isRunning())

    panel._poll_ollama()  # should be a no-op while first is in flight
    check("second poll reuses same thread (no pile-up)",
          panel._check_thread is first)

    check("thread finishes and ref clears", wait_idle(panel) and not first.isRunning())
    check("ref cleared after finished", panel._check_thread is None)

    # A later poll starts a fresh thread
    panel._poll_ollama()
    second = panel._check_thread
    check("new poll starts a new thread", second is not None and second is not first)
    check("status settles connected after ok", wait_idle(panel) and panel._ollama_up is True)
finally:
    requests.get = real_get

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
