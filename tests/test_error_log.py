"""v3.5.0 — persistent error log (roadmap #5). Offscreen, synthetic data only
(CRASH_LOG/ERROR_LOG redirected to a temp dir — never the real store). Covers the
single-generation rotation, sys.excepthook writing tracebacks to app.log (and chaining
to stderr), the PRIVACY guarantee (no local variables / schedule data in the log), and
install_crash_logging() wiring (excepthook set, faulthandler enabled, crash.log rotated).
No Qt needed — these are plain module functions."""
import os, sys, io, tempfile, faulthandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core

TMP = Path(tempfile.mkdtemp())
core.CRASH_LOG = TMP / "crash.log"
core.ERROR_LOG = TMP / "app.log"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

SECRET = "SONS_PRIVATE_BLOCK_TITLE_do_not_log"

def _boom():
    # a local holding "schedule data" — must never reach the log (tracebacks omit locals)
    activities = [{"title": SECRET, "startMin": 600, "endMin": 660}]  # noqa: F841
    raise ValueError("synthetic failure for the log test")

def _exc_info():
    try:
        _boom()
    except ValueError:
        return sys.exc_info()

# ── _rotate_log: single-generation, threshold-gated ───────────────────────────
p = TMP / "rot.log"
p.write_text("small")
core._rotate_log(p, max_bytes=1000)
check("small log is left untouched", p.exists() and not p.with_name("rot.log.old").exists())

p.write_text("x" * 2000)
core._rotate_log(p, max_bytes=1000)
old = p.with_name("rot.log.old")
check("oversize log moved to .old", old.exists() and old.read_text() == "x" * 2000)
check("live log path is cleared after rotation", not p.exists())

p.write_text("y" * 2000)
core._rotate_log(p, max_bytes=1000)
check("rotation replaces a prior .old (one generation)", old.read_text() == "y" * 2000)

# ── log_exception: writes a traceback + header, chains to stderr ───────────────
saved_stderr = sys.stderr
sys.stderr = cap = io.StringIO()          # capture the default-hook output (proves chaining)
try:
    core.log_exception(*_exc_info())
finally:
    sys.stderr = saved_stderr
body = core.ERROR_LOG.read_text(encoding="utf-8")

check("app.log created", core.ERROR_LOG.exists())
check("logs the exception type + message", "ValueError" in body and "synthetic failure" in body)
check("logs the failing function frame", "_boom" in body)
check("header carries version", f"v{core.APP_VERSION}" in body)
check("header carries pid", f"pid {os.getpid()}" in body)
check("still chained to stderr (default hook ran)", "ValueError" in cap.getvalue())

# THE privacy guarantee: a local holding schedule data is NOT in the log
check("PRIVACY: no local variables / schedule data in the log", SECRET not in body)
check("PRIVACY: same for the stderr copy", SECRET not in cap.getvalue())

# a second exception APPENDS (newest-last), not overwrites
sys.stderr = io.StringIO()
try:
    core.log_exception(*_exc_info())
finally:
    sys.stderr = saved_stderr
check("second exception appends (two entries)", core.ERROR_LOG.read_text().count("=====") == 4)

# rotation fires through log_exception at the real 1 MB threshold
core.ERROR_LOG.write_text("z" * 1_100_000)
sys.stderr = io.StringIO()
try:
    core.log_exception(*_exc_info())
finally:
    sys.stderr = saved_stderr
check("log_exception rotates app.log past ~1 MB", core.ERROR_LOG.with_name("app.log.old").exists())
check("post-rotation log holds only the new entry", core.ERROR_LOG.read_text().count("=====") == 2)

# ── install_crash_logging: wires excepthook + faulthandler, rotates crash.log ──
core.CRASH_LOG.write_text("w" * 1_100_000)   # a big prior crash.log should rotate on launch
saved_hook = sys.excepthook
try:
    core.install_crash_logging()
    check("sys.excepthook set to log_exception", sys.excepthook is core.log_exception)
    check("faulthandler enabled", faulthandler.is_enabled())
    check("crash.log opened/created", core.CRASH_LOG.exists())
    check("crash.log rotated on launch", core.CRASH_LOG.with_name("crash.log.old").exists())
finally:
    faulthandler.disable()
    if core._crash_fh:
        core._crash_fh.close()
    sys.excepthook = saved_hook          # restore so we don't leak global state

n = len(results)
print(f"RESULT: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{n})")
sys.exit(0 if all(results) else 1)
