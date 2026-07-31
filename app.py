#!/usr/bin/env python3
"""
Daily Scheduler — Native Desktop App
Pure Python + PySide6 (Qt6). No browser engine.

Entry point only. Everything else lives in focused modules — start with
ARCHITECTURE.md for the map, or core.py for the data model.

What main() does, in order:
  1. --version short-circuit (so packaging smoke tests need no display)
  2. install_crash_logging() FIRST, so anything after it is recorded
  3. apply_theme() before any widget bakes colours
  4. single-instance guard — an ATOMIC QSharedMemory.create(); losers ping the
     winner to surface its window and exit. Never refuses to launch: if the
     guard itself misbehaves, we start anyway
  5. build and show the window (deferred slightly on a --startup launch)

Copyright (C) 2026 Jonathan Shin

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import sys
import os
import getpass
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication,
)
from PySide6.QtCore import (
    QTimer, QThread, QSharedMemory,
)
from PySide6.QtGui import (
    QGuiApplication, QPalette,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# Basename of the XDG desktop entry (no ".desktop"). Must match
# packaging/daily-scheduler.desktop and install-launcher.sh so Linux
# shells (KDE/GNOME) group + pin the window as Daily Scheduler, not python.
DESKTOP_FILE_ID = "daily-scheduler"

import core
import theme
from core import DEFAULT_THEME, install_crash_logging, load_settings
from theme import app_chrome_stylesheet, apply_theme
from mainwindow import MainWindow


# ── Entry point ────────────────────────────────────────────────────────────
def main():
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"Daily Scheduler {core.APP_VERSION}")
        return

    # Persist crashes to ~/.daily-scheduler/{app,crash}.log before anything else can fail.
    install_crash_logging()

    # Apply the saved theme before any widget (or the palette below) bakes colours.
    apply_theme(load_settings().get("theme", DEFAULT_THEME))

    # Register an explicit AppUserModelID so Windows shows our tray toasts as banners
    # (without this, Qt balloon notifications are silently dropped into the action center).
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "DailyScheduler.Planner.1")
        except Exception:
            pass

    # Linux: set the desktop-file id BEFORE QApplication so Wayland app_id / X11
    # WM_CLASS match daily-scheduler.desktop — required for taskbar pin/group.
    if sys.platform.startswith("linux"):
        QGuiApplication.setDesktopFileName(DESKTOP_FILE_ID)

    app = QApplication(sys.argv)
    app.setApplicationName("Daily Scheduler")
    app.setApplicationDisplayName("Daily Scheduler")
    app.setOrganizationName("DailyScheduler")
    app.setApplicationVersion(core.APP_VERSION)
    # Re-assert after construction (some Qt builds only read it from the instance).
    if sys.platform.startswith("linux"):
        app.setDesktopFileName(DESKTOP_FILE_ID)
    app.setStyle("Fusion")
    # Keep running in the tray after the window is closed, so reminders still fire.
    app.setQuitOnLastWindowClosed(False)

    pal = app.palette()
    pal.setColor(QPalette.Window,          theme.C_BG)
    pal.setColor(QPalette.WindowText,      theme.C_TEXT)
    pal.setColor(QPalette.Base,            theme.C_SURF2)
    pal.setColor(QPalette.AlternateBase,   theme.C_SURFACE)
    pal.setColor(QPalette.Text,            theme.C_TEXT)
    pal.setColor(QPalette.Button,          theme.C_SURFACE)
    pal.setColor(QPalette.ButtonText,      theme.C_TEXT)
    pal.setColor(QPalette.Highlight,       theme.C_ACCENT)
    pal.setColor(QPalette.HighlightedText, theme.C_ON_ACCENT)
    pal.setColor(QPalette.ToolTipBase,     theme.C_SURF2)
    pal.setColor(QPalette.ToolTipText,     theme.C_TEXT)
    app.setPalette(pal)
    app.setStyleSheet(app_chrome_stylesheet())

    # ── Single instance ──────────────────────────────────────────────────────
    # Detect a running copy with an ATOMIC shared-memory create — race-safe at boot,
    # where Windows can launch several copies at once (an earlier listen()-based guard
    # let racing copies survive as zombies). Exactly one create() succeeds; every other
    # launch exits after pinging the winner to surface its window. A QLocalServer carries
    # that "show" ping. Wrapped so it can never block launch. On Windows the OS frees the
    # segment when the process ends, so a crash leaves no stale lock.
    try:
        _instance_user = getpass.getuser()   # portable (USERNAME on Win, USER/pwd on Linux)
    except Exception:
        _instance_user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    _key = "DailyScheduler.instance." + _instance_user
    _guard, _server = None, None
    try:
        _guard = QSharedMemory(_key)
        got_lock = _guard.create(1)
        if not got_lock:
            # Linux (and some crash paths): a dead process can leave the segment.
            # Attach+detach clears an orphan; only exit if a live server answers.
            try:
                if _guard.attach():
                    _guard.detach()
            except Exception:
                pass
            got_lock = _guard.create(1)
        if got_lock:
            QLocalServer.removeServer(_key)
            _server = QLocalServer()
            _server.listen(_key)
        else:
            # Another live copy holds the lock — surface its window, then exit.
            def _ping_running_instance() -> bool:
                """Tell the winner to show its window. Returns True if a live
                server accepted the connection. Never calls waitForBytesWritten
                unless still Connected (avoids Qt's UnconnectedState warning)."""
                sock = QLocalSocket()
                sock.connectToServer(_key)
                if not sock.waitForConnected(800):
                    sock.abort()
                    return False
                try:
                    sock.write(b"show")
                    sock.flush()
                    if sock.state() == QLocalSocket.ConnectedState:
                        sock.waitForBytesWritten(400)
                    if sock.state() == QLocalSocket.ConnectedState:
                        sock.waitForDisconnected(200)
                except Exception:
                    pass
                try:
                    sock.disconnectFromServer()
                except Exception:
                    pass
                sock.abort()
                return True

            if _ping_running_instance():
                try: _guard.detach()
                except Exception: pass
                return
            # No live server: try one more orphan clear, then start anyway.
            try:
                if _guard.attach():
                    _guard.detach()
                if _guard.create(1):
                    QLocalServer.removeServer(_key)
                    _server = QLocalServer()
                    _server.listen(_key)
                else:
                    # A LIVE process holds the segment but isn't answering yet.
                    # At boot several copies launch at once and the winner may sit
                    # in the µs window between create(1) and listen() — falling
                    # through unguarded here reintroduced the 2.5.1 duplicate-
                    # instance race. Give the winner a moment and re-ping before
                    # the last-resort unguarded start (kept so a wedged holder
                    # can never make the app refuse to launch — v2.5.3 lesson).
                    surfaced = False
                    for _ in range(3):
                        QThread.msleep(500)
                        if _ping_running_instance():
                            surfaced = True
                            break
                    if surfaced:
                        try: _guard.detach()
                        except Exception: pass
                        return
                    try: _guard.detach()
                    except Exception: pass
                    # Fall through without lock rather than refusing to launch.
                    _guard, _server = None, None
            except Exception:
                _guard, _server = None, None
    except Exception:
        _guard, _server = None, None   # never let the guard stop the app from launching

    # Startup diagnostic: one line per SURVIVING launch (duplicates return above and
    # never reach here). If a post-boot read of this log shows two lines with the same
    # boot timestamp, a second instance slipped past the guard. No schedule data logged.
    try:
        with open(core.DATA_DIR / "startup.log", "a", encoding="utf-8") as _lf:
            _lf.write(f"{datetime.now().isoformat()} pid={os.getpid()} "
                      f"argv={sys.argv[1:]} guard={'won' if _server is not None else 'fell-through'}\n")
    except Exception:
        pass

    # The window is built via _build_window() — immediately for a normal launch, but
    # DEFERRED at Windows sign-in (see the startup-delay block below).
    holder: dict[str, MainWindow | None] = {"win": None}

    def _build_window():
        if holder["win"] is not None:
            return
        win = MainWindow()
        holder["win"] = win
        # Centre on the primary screen, then show. We always show the window (a
        # hidden-to-tray start was unreachable when the tray icon failed to render at
        # boot); it still lives in the tray after you close it (see closeEvent), and
        # _setup_tray() retries/re-asserts so that icon is reliable.
        geo = app.primaryScreen().availableGeometry()
        win.move((geo.width() - win.width()) // 2, (geo.height() - win.height()) // 2)
        win.show(); win.raise_(); win.activateWindow()

    # A second launch pings our server → surface the window (building it first if we're
    # still inside the startup delay and it doesn't exist yet).
    if _server is not None:
        def _surface():
            try:
                conn = _server.nextPendingConnection()
                if conn is not None:
                    # Drain any payload so the peer can finish cleanly.
                    if conn.waitForReadyRead(50):
                        conn.readAll()
                    conn.disconnectFromServer()
                    conn.close()
            except Exception:
                pass
            try:
                _build_window()
                w = holder.get("win")
                if w is not None:
                    if hasattr(w, "_show_from_tray"):
                        w._show_from_tray()
                    else:
                        w.show(); w.raise_(); w.activateWindow()
            except Exception:
                pass
        _server.newConnection.connect(_surface)

    # At Windows sign-in (--startup) the GPU driver can crash/reset for the first ~minute
    # (observed on this machine: AMD atiadlxx.dll fault + a GPU watchdog TDR right at boot).
    # A window built during that window can't paint, leaving the app in Task Manager with
    # no visible window. So DELAY building the window until the GPU has settled. The real
    # fix is disabling Windows Fast Startup / a GPU driver reinstall — this is a safety net.
    # startup_delay_sec (settings.json, default 60) is configurable; 0 disables the delay.
    if "--startup" in sys.argv:
        delay = max(0, int(load_settings().get("startup_delay_sec", 60) or 0))
        if delay:
            QTimer.singleShot(delay * 1000, _build_window)
        else:
            _build_window()
    else:
        _build_window()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
