"""v3.2.0 — auto-update check (roadmap #2). Offscreen, no network: requests.get is
monkeypatched to a fake, so nothing actually hits GitHub. Covers the version-compare
helpers, UpdateCheckThread's fail-silent behaviour on every response class, and the
MainWindow wiring (status pill, opt-out setting, releases-page open)."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core
import mainwindow
import platform_utils
import requests
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl

TMP = Path(tempfile.mkdtemp())
core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.CREDS_FILE    = TMP / "credentials.json"   # absent → MainWindow won't auto-boot/auth
core.TOKEN_FILE    = TMP / "token.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)

# ── Pure version helpers ─────────────────────────────────────────────────────
check("strip_v drops leading v", core.strip_v("v3.2.0") == "3.2.0")
check("strip_v leaves bare version", core.strip_v("3.2.0") == "3.2.0")
check("_version_tuple parses", core._version_tuple("v3.2.0") == (3, 2, 0))
check("_version_tuple drops pre-release", core._version_tuple("3.2.0-beta.2") == (3, 2, 0))
check("_version_tuple garbage → ()", core._version_tuple("latest") == ())

check("newer patch", core.is_newer_version("3.1.1", "3.1.0"))
check("newer minor", core.is_newer_version("3.2.0", "3.1.0"))
check("newer major w/ v", core.is_newer_version("v4.0.0", "3.9.9"))
check("same is not newer", not core.is_newer_version("3.2.0", "3.2.0"))
check("older is not newer", not core.is_newer_version("3.1.0", "3.2.0"))
check("zero-pad equal (3.2 == 3.2.0)", not core.is_newer_version("3.2", "3.2.0"))
check("garbage tag never nags", not core.is_newer_version("nightly", "3.2.0"))
check("empty tag never nags", not core.is_newer_version("", "3.2.0"))

# ── UpdateCheckThread: fail-silent across every response class ────────────────
class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
    def json(self):
        return self._payload

real_get = requests.get
def fake_get(url, **kw):
    return fake_get.resp
def run_thread():
    emitted = []
    t = platform_utils.UpdateCheckThread()
    t.update_available.connect(lambda tag, url: emitted.append((tag, url)))
    t.run()                       # synchronous — same thread, direct connection
    return emitted

requests.get = fake_get
try:
    fake_get.resp = FakeResp(200, {"tag_name": "v99.0.0",
                                   "html_url": "https://example/rel/99"})
    em = run_thread()
    check("200 + newer → emits", em == [("v99.0.0", "https://example/rel/99")])

    fake_get.resp = FakeResp(200, {"tag_name": f"v{core.APP_VERSION}"})
    check("200 + same version → silent", run_thread() == [])

    fake_get.resp = FakeResp(200, {"tag_name": "v0.0.1"})
    check("200 + older → silent", run_thread() == [])

    fake_get.resp = FakeResp(404, {})           # repo still private / no releases
    check("404 (private repo) → silent", run_thread() == [])

    fake_get.resp = FakeResp(403, {})           # rate-limited
    check("403 (rate limit) → silent", run_thread() == [])

    def boom(url, **kw):                         # offline / DNS failure / timeout
        raise requests.exceptions.ConnectionError("offline")
    requests.get = boom
    check("network error → silent", run_thread() == [])

    # falls back to the releases page when the payload omits html_url
    requests.get = fake_get
    fake_get.resp = FakeResp(200, {"tag_name": "v99.0.0"})
    em = run_thread()
    check("missing html_url falls back to releases page", em == [("v99.0.0", core.RELEASES_PAGE)])
finally:
    requests.get = real_get

# ── MainWindow wiring ────────────────────────────────────────────────────────
mw = mainwindow.MainWindow()
# NB: isVisible() is False offscreen (parent window unshown), so assert the widget's
# own hidden state via isHidden().
check("update pill hidden by default", mw._update_btn.isHidden())

mw._on_update_available("v3.5.0", "https://example/rel/350")
check("pill shows on update", not mw._update_btn.isHidden())
check("pill text names the version", "v3.5.0" in mw._update_btn.text())
check("update url stored", mw._update_url == "https://example/rel/350")

opened = []
real_open = QDesktopServices.openUrl
QDesktopServices.openUrl = staticmethod(lambda u: opened.append(u.toString()))
try:
    mw._open_releases_page()
    check("open releases uses stored url", opened == ["https://example/rel/350"])
finally:
    QDesktopServices.openUrl = real_open

# opt-out: no thread starts when the setting is off
mw._update_thread = None
mw._settings["update_check_on"] = False
mw._check_for_update()
check("setting off → no check runs", mw._update_thread is None)

# guard: a check already in flight is not replaced
mw._settings["update_check_on"] = True
class Busy:
    def isRunning(self): return True
sentinel = Busy()
mw._update_thread = sentinel
mw._check_for_update()
check("in-flight check not restarted", mw._update_thread is sentinel)

n = len(results)
print(f"RESULT: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{n})")
sys.exit(0 if all(results) else 1)
