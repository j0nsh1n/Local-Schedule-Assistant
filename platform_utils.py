"""Daily Scheduler — run-at-login, alert sounds, update check.

Where the OS-specific behaviour is quarantined. Anything that branches on
Windows vs. Linux belongs here rather than in the widget modules.

Contents
    Desktop notifications  show_desktop_notification() — FreeDesktop/gdbus.
                           Linux only (returns False everywhere else, which is
                           what routes Windows to the tray/AlertPopup path).
                           Used because Wayland won't let a client position its
                           own toast; the notification daemon owns placement.
    Run-at-login ......... set_startup / is_startup_enabled.
                           Windows: a Startup-folder .lnk (NOT a registry Run
                           key — that worked but never showed in Task Manager).
                           Linux: ~/.config/autostart/*.desktop.
                           Both pass --startup. The shortcut records an absolute
                           path, so it must be rewritten if the app moves.
    Alert tones .......... NOTIFY_TONES + WAV synthesis with the stdlib.
                           A file is needed because QApplication.beep() is
                           silent under PipeWire.
    play_alert_sound ..... QSoundEffect, falling back to MessageBeep / beep()
    UpdateCheckThread .... polls the GitHub releases API. Notify-only: it never
                           downloads anything, and fails silently on every
                           non-200 (offline, rate-limited, 404).

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

import sys
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional

import requests
from PySide6.QtWidgets import (
    QApplication,
)
from PySide6.QtCore import (
    QThread, Signal,
)

import core
from core import LATEST_RELEASE_API, RELEASES_PAGE, is_newer_version


# ── Desktop notifications (Linux) ───────────────────────────────────────────
# On Wayland, Qt cannot freely position frameless "toast" windows — the
# compositor centers them. FreeDesktop Notifications (Plasma's daemon) place
# alerts in the configured corner. Prefer that path on Linux; custom AlertPopup
# remains the Windows / fallback path.

def show_desktop_notification(
    title: str,
    body: str,
    *,
    timeout_ms: int = 12000,
    urgency: int = 2,
    icon: str = "daily-scheduler",
) -> bool:
    """Show a system notification via org.freedesktop.Notifications (gdbus).

    urgency: 0=low, 1=normal, 2=critical (helps pierce Do Not Disturb on KDE).
    Returns True if the call was accepted. Best-effort; never raises."""
    if platform.system() != "Linux":
        return False
    # gdbus is always available on modern Fedora/KDE; notify-send is broken on
    # some Nobara setups (libnotify symbol mismatch), so we don't use it.
    title = (title or "Daily Scheduler").replace("\x00", "")
    body = (body or "").replace("\x00", "")
    urgency = 0 if urgency < 0 else 2 if urgency > 2 else int(urgency)
    timeout_ms = max(1000, min(int(timeout_ms), 60000))
    # GVariant dict for hints. desktop-entry ties the toast to our .desktop file.
    hints = (
        f"{{'urgency': <byte 0x{urgency:x}>, "
        f"'desktop-entry': <'daily-scheduler'>, "
        f"'category': <'reminder'>}}"
    )
    try:
        r = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications",
                "--method", "org.freedesktop.Notifications.Notify",
                "Daily Scheduler",
                "0",
                icon or "dialog-information",
                title,
                body,
                "[]",
                hints,
                f"int32 {timeout_ms}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and "uint32" in (r.stdout or ""):
            return True
        # Retry with a generic icon if our app icon name isn't known to the daemon.
        if icon and icon != "dialog-information":
            return show_desktop_notification(
                title, body, timeout_ms=timeout_ms, urgency=urgency,
                icon="dialog-information",
            )
        return False
    except Exception:
        return False


# ── Run-at-login ─────────────────────────────────────────────────────────────
# Windows: a .lnk in the user's Startup folder — the most visible/reliable method; it
# shows in Task Manager > Startup and Settings, and the user can see the file directly.
# (The old HKCU Run-key method worked at boot but Task Manager was slow to display it.)
# Linux: an XDG autostart entry (~/.config/autostart/daily-scheduler.desktop) — the
# freedesktop standard, honoured by KDE/GNOME and visible in their autostart settings.
_RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "DailyScheduler"

def _startup_lnk() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (Path(base) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup" / "Daily Scheduler.lnk")

def _entry_script() -> Path:
    """Path to app.py (the real entry point). After the v4.2 module split this
    file is platform_utils.py — never point Startup/XDG shortcuts here."""
    return Path(__file__).resolve().parent / "app.py"

def _installed_launcher() -> Optional[Path]:
    """User-level command installed by install-launcher.sh (~/.local/bin/daily-scheduler).
    Prefer this for XDG autostart so login launch matches the pin-able desktop app."""
    name = "daily-scheduler"
    candidates = []
    xdg_bin = os.environ.get("XDG_BIN_HOME")
    if xdg_bin:
        candidates.append(Path(xdg_bin) / name)
    candidates.append(Path.home() / ".local" / "bin" / name)
    for p in candidates:
        try:
            if p.is_file() and os.access(p, os.X_OK):
                return p
        except OSError:
            continue
    return None

def _startup_target():
    """(target, arguments, working_dir) the shortcut should launch — the APP only,
    never Ollama, with --startup so it opens quietly into the tray."""
    if getattr(sys, "frozen", False):                  # packaged .exe
        return sys.executable, "--startup", str(Path(sys.executable).parent)
    # Prefer the pin-able no-extension launcher when install-launcher.sh was run.
    launcher = _installed_launcher()
    if launcher is not None:
        return str(launcher), "--startup", str(_entry_script().parent)
    script = _entry_script()                           # running from source
    return sys.executable, f'"{script}" --startup', str(script.parent)

def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"

def _remove_legacy_run_key():
    """Drop the old HKCU Run entry so we don't launch twice after migrating."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _RUN_NAME)
    except OSError:
        pass

def _autostart_desktop() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "daily-scheduler.desktop"

def _desktop_exec_line() -> str:
    """Exec= value for the autostart entry. Desktop-entry spec: arguments with spaces
    must be double-quoted, and a literal `"` or `\\` inside them backslash-escaped."""
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    target, args, _ = _startup_target()
    if getattr(sys, "frozen", False):
        return f"{q(target)} {args}"
    launcher = _installed_launcher()
    if launcher is not None:
        return f"{q(str(launcher))} --startup"
    script = _entry_script()
    return f"{q(target)} {q(str(script))} --startup"

def _set_startup_linux(enabled: bool) -> bool:
    entry = _autostart_desktop()
    if not enabled:
        entry.unlink(missing_ok=True)
        return not entry.exists()
    _, _, workdir = _startup_target()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Daily Scheduler\n"
        "Comment=Daily planner with a local AI assistant\n"
        f"Exec={_desktop_exec_line()}\n"
        f"Path={workdir}\n"
        "Icon=daily-scheduler\n"
        "Terminal=false\n"
        "StartupWMClass=daily-scheduler\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    return entry.exists()

def is_startup_enabled() -> bool:
    if platform.system() == "Windows":
        return _startup_lnk().exists()
    if platform.system() == "Linux":
        return _autostart_desktop().exists()
    return False

def set_startup(enabled: bool) -> bool:
    """Create/remove the run-at-login entry. No admin rights needed."""
    if platform.system() == "Linux":
        try:
            return _set_startup_linux(enabled)
        except Exception:
            return False
    if platform.system() != "Windows":
        return False
    try:
        lnk = _startup_lnk()
        if enabled:
            target, args, workdir = _startup_target()
            lnk.parent.mkdir(parents=True, exist_ok=True)
            ps = (
                "$ws = New-Object -ComObject WScript.Shell; "
                f"$s = $ws.CreateShortcut({_ps_quote(str(lnk))}); "
                f"$s.TargetPath = {_ps_quote(target)}; "
                f"$s.Arguments = {_ps_quote(args)}; "
                f"$s.WorkingDirectory = {_ps_quote(workdir)}; "
                "$s.Description = 'Daily Scheduler'; $s.Save()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, creationflags=0x08000000,
                timeout=15,
            )
            ok = lnk.exists()
        else:
            if lnk.exists():
                lnk.unlink()
            ok = not lnk.exists()
        _remove_legacy_run_key()       # migrate away from the old Run-key method
        return ok
    except Exception:
        return False


# Built-in alert tones (id → label). Synthesized to ~/.daily-scheduler/tones/.
NOTIFY_TONES = [
    ("chime",  "Chime (default)"),
    ("soft",   "Soft ping"),
    ("bright", "Bright ping"),
    ("low",    "Low thump"),
    ("glass",  "Glass tap"),
]

def _synth_tone_wav(path: Path, tone_id: str) -> bool:
    """Write a short mono WAV for `tone_id`. Returns True on success."""
    try:
        import wave, math, struct
        rate = 44100
        frames = bytearray()

        def blip(freq, secs, vol=0.45):
            n = max(1, int(rate * secs))
            for i in range(n):
                # Attack / decay envelope so samples never click
                env = min(1.0, i / 280.0, (n - i) / 900.0)
                if freq <= 0:
                    sample = 0.0
                else:
                    t = i / rate
                    # Slight 2nd harmonic for a less sterile beep
                    sample = math.sin(2 * math.pi * freq * t)
                    sample += 0.18 * math.sin(4 * math.pi * freq * t)
                frames.extend(struct.pack("<h", int(32767 * vol * env * sample)))

        tid = (tone_id or "chime").lower()
        if tid == "soft":
            blip(520, 0.12, 0.35); blip(0, 0.04); blip(620, 0.18, 0.28)
        elif tid == "bright":
            blip(880, 0.08, 0.4); blip(0, 0.03); blip(1175, 0.1, 0.38); blip(0, 0.02); blip(1397, 0.14, 0.32)
        elif tid == "low":
            blip(180, 0.22, 0.55); blip(0, 0.04); blip(140, 0.28, 0.4)
        elif tid == "glass":
            blip(1480, 0.06, 0.32); blip(0, 0.02); blip(1760, 0.2, 0.22)
        else:  # chime
            blip(660, 0.14, 0.45); blip(0, 0.06); blip(880, 0.22, 0.4)

        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(bytes(frames))
        return True
    except Exception:
        return False

def ensure_alert_wav(tone_id: str = "chime") -> Optional[Path]:
    """Return a reusable WAV path for the given tone (stdlib synthesis)."""
    tid = (tone_id or "chime").lower()
    if tid not in {t[0] for t in NOTIFY_TONES}:
        tid = "chime"
    try:
        p = core.DATA_DIR / "tones" / f"alert_{tid}.wav"
        # Re-synth if missing or empty (allows tone set to grow without stale files)
        if not p.exists() or p.stat().st_size < 64:
            if not _synth_tone_wav(p, tid):
                return None
        return p
    except Exception:
        return None

def play_alert_sound(parent=None, *, tone: str = "chime", volume: float = 0.8) -> None:
    """Play a short alert tone. `volume` is 0..1. Uses Qt multimedia when available;
    falls back to Windows MessageBeep or QApplication.beep()."""
    try:
        vol = max(0.0, min(1.0, float(volume)))
    except (TypeError, ValueError):
        vol = 0.8
    if vol <= 0.001:
        return
    # Prefer synthesized WAV so the chosen tone is audible on all platforms
    try:
        wav = ensure_alert_wav(tone)
        if wav:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            # Reuse one QSoundEffect on the parent when possible so rapid previews
            # don't pile up player objects.
            fx = None
            if parent is not None:
                fx = getattr(parent, "_alert_fx", None)
            if fx is None:
                fx = QSoundEffect(parent)
                if parent is not None:
                    parent._alert_fx = fx
            path = str(wav)
            if getattr(fx, "_tone_path", None) != path:
                fx.setSource(QUrl.fromLocalFile(path))
                fx._tone_path = path  # type: ignore[attr-defined]
            fx.setVolume(vol)
            fx.play()
            return
    except Exception:
        pass
    try:
        if platform.system() == "Windows":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
    except Exception:
        pass
    try:
        QApplication.beep()
    except Exception:
        pass


class UpdateCheckThread(QThread):
    """Ask GitHub, once, whether a newer release exists. Fails SILENTLY on ANY
    error — offline, timeout, 404 (repo still private / no releases yet), or a
    403 rate-limit — because an update check must never interrupt the planner.
    Emits update_available ONLY when the latest published tag is strictly newer
    than core.APP_VERSION. GitHub requires a User-Agent or it returns 403."""
    update_available = Signal(str, str)   # tag_name, html_url

    def run(self):
        try:
            r = requests.get(
                LATEST_RELEASE_API, timeout=(5, 8),
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": f"DailyScheduler/{core.APP_VERSION}"},
            )
            if r.status_code != 200:      # 404 while private, 403 rate-limited, …
                return
            data = r.json()
            tag = str(data.get("tag_name", "")).strip()
            url = str(data.get("html_url") or RELEASES_PAGE)
            if is_newer_version(tag, core.APP_VERSION):
                self.update_available.emit(tag, url)
        except Exception:
            pass
