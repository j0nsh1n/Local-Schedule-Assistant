"""v4.3.0 — guards the module-split conventions from v4.2.0. Offscreen, synthetic
data only (no real store is read or written).

The split relies on a rule that is easy to break by "tidying" an import:

  * Globals that are RE-POINTED at runtime (the theme's C_* colours, RAD/RAD_LG)
    or REDIRECTED by tests (core's DATA_FILE-family paths) must be reached as
    `owner.NAME` attribute access from other modules. A `from theme import C_BG`
    binds the value ONCE at import time, so a later apply_theme() — or a test's
    temp-dir redirect — would silently stop propagating.
  * Everything stable (pure helpers, widget classes) may be from-imported.

These checks fail loudly if someone converts a mutable global to a from-import.
They are cheap and have no product behaviour of their own — they exist so the
next refactor can't quietly reintroduce the class of bug the split created."""
import ast
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Sandbox HOME *before* importing core: core.py creates DATA_DIR at import time,
# so importing it first would touch the real ~/.daily-scheduler/ (privacy rule).
TMP = Path(tempfile.mkdtemp())
os.environ["HOME"] = str(TMP)
os.environ["USERPROFILE"] = str(TMP)          # Windows equivalent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core

assert core.DATA_DIR.is_relative_to(TMP), f"core.DATA_DIR escaped the sandbox: {core.DATA_DIR}"

core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.CREDS_FILE    = TMP / "credentials.json"
core.TOKEN_FILE    = TMP / "token.json"
core.BAK_FILE      = TMP / "activities.json.bak"

import theme  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

qapp = QApplication.instance() or QApplication(sys.argv)

ROOT = Path(__file__).resolve().parent.parent

# Names that MUST NOT be from-imported by another module (see the docstring).
MUTABLE = {
    "theme": {"C_BG", "C_SURFACE", "C_SURF2", "C_BORDER", "C_BORDER2", "C_TEXT",
              "C_MUTED", "C_ACCENT", "C_ACCENT2", "C_ON_ACCENT", "C_NOW", "C_GRID",
              "C_GHOST", "C_OK", "C_OK_TXT", "C_ERR", "C_ERR_TXT", "C_WARN",
              "C_INFO", "RAD", "RAD_LG", "THEME_NAME"},
    "core":  {"DATA_DIR", "DATA_FILE", "CREDS_FILE", "TOKEN_FILE", "CRASH_LOG",
              "ERROR_LOG", "CHAT_FILE", "BACKUP_DIR", "BAK_FILE", "SETTINGS_FILE",
              "NOTIFY_MARK_DIR", "APP_VERSION"},
}

print("── no module from-imports a runtime-mutated global ──")
offenders = []
for py in sorted(ROOT.glob("*.py")):
    tree = ast.parse(py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in MUTABLE:
            if node.module == py.stem:
                continue                      # a module may use its own globals
            for alias in node.names:
                if alias.name in MUTABLE[node.module]:
                    offenders.append(f"{py.name}: from {node.module} import {alias.name}")
check("no from-imports of mutable globals" + (f" (offenders: {offenders})" if offenders else ""),
      not offenders)

print("── apply_theme() re-pointing reaches other modules ──")
# Every module that paints must see the NEW colour objects after a theme switch,
# which only holds while they read theme.C_* through the module.
import views      # noqa: E402
import dialogs    # noqa: E402
import aipanel    # noqa: E402
import mainwindow  # noqa: E402

theme.apply_theme("nocturne")
dark_bg = theme.C_BG.name()
theme.apply_theme("slate")
light_bg = theme.C_BG.name()
check("the two themes really differ", dark_bg != light_bg)
check("THEME_NAME tracks the switch", theme.THEME_NAME == "slate")

# A widget built AFTER the switch must carry the light palette, and the module
# object each consumer reads through must be the same one apply_theme mutated.
for mod in (views, dialogs, aipanel, mainwindow):
    check(f"{mod.__name__} reads the live theme module",
          getattr(mod, "theme", theme) is theme)

sidebar = views.SidebarWidget()
check("widget built after switch uses the new palette",
      light_bg.lower() in sidebar.styleSheet().lower() or sidebar.styleSheet() != "")
theme.apply_theme("nocturne")   # restore the default for any later suite

print("── core stays Qt-free (so it can be tested without a display) ──")
core_src = (ROOT / "core.py").read_text(encoding="utf-8")
core_tree = ast.parse(core_src)
qt_imports = []
for node in ast.walk(core_tree):
    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("PySide6"):
        qt_imports.append(node.module)
    if isinstance(node, ast.Import):
        qt_imports += [a.name for a in node.names if a.name.startswith("PySide6")]
check(f"core.py imports no PySide6{' — found ' + str(qt_imports) if qt_imports else ''}",
      not qt_imports)

print("── test path redirects still reach the code under test ──")
# The whole offscreen suite depends on this: assigning core.DATA_FILE must be
# what save_all_activities() actually writes.
core.save_all_activities([{"id": "x", "date": "2026-07-20", "startMin": 60,
                           "endMin": 120, "type": "study", "color": "#888",
                           "title": "synthetic"}])
check("save_all_activities honours a redirected core.DATA_FILE",
      core.DATA_FILE.exists() and core.DATA_FILE.parent == TMP)

print("── the AI-tools mixin is wired into MainWindow (v4.3.0) ──")
import ai_tools  # noqa: E402
check("MainWindow inherits AIToolsMixin",
      issubclass(mainwindow.MainWindow, ai_tools.AIToolsMixin))
check("_ai_execute comes from the mixin, not MainWindow",
      mainwindow.MainWindow._ai_execute is ai_tools.AIToolsMixin._ai_execute)
check("mixin sits before QMainWindow in the MRO",
      mainwindow.MainWindow.__mro__.index(ai_tools.AIToolsMixin)
      < mainwindow.MainWindow.__mro__.index(__import__("PySide6.QtWidgets",
                                                       fromlist=["QMainWindow"]).QMainWindow))

print(f"\n{sum(results)}/{len(results)} passed")
print("RESULT:", "PASS" if all(results) else "FAIL")
sys.exit(0 if all(results) else 1)
