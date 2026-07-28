"""v4.6.2 — show_desktop_notification(): the Linux FreeDesktop alert path.

The live daemon is NOT called here. CI runners have no session bus and no
notification daemon, and a real call on a dev box would spam the desktop — so
`subprocess` and `platform` are swapped for fakes and we assert on the command
that WOULD have been sent.

The most important check is the non-Linux early return: on Windows this
function must do nothing at all and let the tray/AlertPopup path run. If that
guard ever breaks, every Windows alert would shell out to a `gdbus` that isn't
there.

Synthetic data only; HOME is sandboxed before core is imported (core creates
DATA_DIR at import time)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TMP = Path(tempfile.mkdtemp())
os.environ["HOME"] = str(TMP)
os.environ["USERPROFILE"] = str(TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402

assert core.DATA_DIR.is_relative_to(TMP), f"DATA_DIR escaped the sandbox: {core.DATA_DIR}"

import platform_utils  # noqa: E402

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


class _FakePlatform:
    def __init__(self, name):
        self._name = name
    def system(self):
        return self._name


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="(uint32 7,)"):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class _FakeSubprocess:
    """Records calls instead of running them."""
    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result if result is not None else _FakeCompleted()
        self._raises = raises
    def run(self, argv, **kw):
        self.calls.append((argv, kw))
        if self._raises is not None:
            raise self._raises
        r = self._result
        return r(len(self.calls)) if callable(r) else r


_real_platform = platform_utils.platform
_real_subprocess = platform_utils.subprocess

def call(*, system="Linux", result=None, raises=None, **kwargs):
    """Run show_desktop_notification under fakes; returns (ok, fake_subprocess)."""
    fake = _FakeSubprocess(result=result, raises=raises)
    platform_utils.platform = _FakePlatform(system)
    platform_utils.subprocess = fake
    try:
        ok = platform_utils.show_desktop_notification(
            kwargs.pop("title", "T"), kwargs.pop("body", "B"), **kwargs)
    finally:
        platform_utils.platform = _real_platform
        platform_utils.subprocess = _real_subprocess
    return ok, fake


print("── non-Linux must be a complete no-op (protects the Windows path) ──")
for osname in ("Windows", "Darwin"):
    ok, fake = call(system=osname)
    check(f"{osname}: returns False", ok is False)
    check(f"{osname}: no subprocess call at all", fake.calls == [])

print("── Linux success path ──")
ok, fake = call()
check("returns True when the daemon replies with an id", ok is True)
check("exactly one call", len(fake.calls) == 1)
argv = fake.calls[0][0]
check("invokes gdbus", argv[0] == "gdbus")
check("targets the Notifications service",
      "org.freedesktop.Notifications" in argv)
check("calls the Notify method",
      "org.freedesktop.Notifications.Notify" in argv)
check("has a timeout so a wedged bus can't hang the GUI",
      fake.calls[0][1].get("timeout") == 5)
check("captures output (never leaks to the console)",
      fake.calls[0][1].get("capture_output") is True)

print("── a reply without an id is not success ──")
ok, _ = call(result=_FakeCompleted(returncode=0, stdout="weird"))
check("rc 0 but no uint32 -> False", ok is False)

print("── urgency is clamped and encoded as a GVariant byte ──")
for given, want in ((2, "0x2"), (1, "0x1"), (0, "0x0"), (9, "0x2"), (-4, "0x0")):
    ok, fake = call(urgency=given)
    hints = " ".join(fake.calls[0][0])
    check(f"urgency {given} -> byte {want}", f"<byte {want}>" in hints)

print("── timeout is clamped to a sane range ──")
for given, want in ((12000, 12000), (10, 1000), (999999, 60000)):
    ok, fake = call(timeout_ms=given)
    check(f"timeout {given} -> {want}", f"int32 {want}" in fake.calls[0][0])

print("── unknown icon retries once with a generic one, then gives up ──")
ok, fake = call(result=_FakeCompleted(returncode=1, stdout=""), icon="daily-scheduler")
check("failed call -> False", ok is False)
check("retried exactly once (no infinite recursion)", len(fake.calls) == 2)
check("first attempt used the app icon", "daily-scheduler" in fake.calls[0][0])
check("retry used the generic icon", "dialog-information" in fake.calls[1][0])

ok, fake = call(result=_FakeCompleted(returncode=1, stdout=""), icon="dialog-information")
check("no retry when already generic", len(fake.calls) == 1)

print("── failures are swallowed: an alert must never crash the app ──")
ok, fake = call(raises=OSError("gdbus missing"))
check("OSError -> False", ok is False)
ok, fake = call(raises=RuntimeError("bus timeout"))
check("RuntimeError -> False", ok is False)

print("── hostile / empty text is sanitised ──")
ok, fake = call(title="a\x00b", body="c\x00d")
joined = " ".join(fake.calls[0][0])
check("NUL bytes stripped from title/body", "\x00" not in joined)
ok, fake = call(title="", body="")
check("empty title falls back to the app name",
      "Daily Scheduler" in " ".join(fake.calls[0][0]))

print(f"\n{sum(results)}/{len(results)} passed")
print("RESULT:", "PASS" if all(results) else "FAIL")
sys.exit(0 if all(results) else 1)
