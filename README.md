# Local Schedule Assistant

A native desktop daily planner with a **local, private AI assistant** that can read and
edit your calendar by voice/text — all running on your own machine. No browser engine,
no cloud: the UI is native Qt, and the AI runs locally through [Ollama](https://ollama.com).

Built with Python + PySide6 (Qt6).

---

## Features

- **Day / Month / Year views** — a Google-Calendar-style timeline. Navigate with ‹ Today ›.
- **Direct editing** — drag on the timeline to create a block, drag a block to move it,
  drag its edges to resize, click (or right-click → Edit) to change its title/type/time,
  right-click → Delete to remove it.
- **Local AI assistant** — chat with a local LLM that edits your schedule directly:
  - *"Add a study block from 2 to 4pm"*
  - *"Shift everything 2 hours later"*
  - *"Copy my schedule to 6/14"*
  - *"Split my afternoon into 30-minute study blocks with breaks"*
  - It calls real tools (`add_block`, `move_block`, `delete_block`, `clear_day`,
    `shift_blocks`, `copy_day`, `replace_day`, `list_blocks`), verifies its own work,
    and the app enforces a conflict-free schedule.
- **Desktop notifications** — a system-tray toast when a block starts.
- **Start with Windows** — optional; opens quietly into the tray (the AI server is *not*
  started automatically — only when you press ▶).
- **Optional Google Calendar** — overlays your real (read-only) events.
- **Private by design** — your schedule never leaves the machine; the AI runs on-device.

---

## Quick start (run from source)

Requires **Python 3.10+** on Windows.

```bat
run.bat
```

`run.bat` installs the dependencies and launches the app. Or manually:

```bat
pip install -r requirements.txt
python app.py
```

### Prebuilt executable
A ready-to-run `DailyScheduler.exe` (no Python needed) is attached to the
[latest release](../../releases/latest).

---

## Enabling the AI assistant

1. Install [Ollama](https://ollama.com/download).
2. Pull a model (the app defaults to `qwen2.5:14b`, which is strong at tool use):
   ```bat
   ollama pull qwen2.5:14b
   ```
   Lighter alternatives: `qwen2.5:7b` (faster) or `llama3.1:8b`. You can change the model
   in the AI panel's **Model** field.
3. In the app, open the AI panel and press **▶** to start the local server. Use **⏏** to
   unload the model from memory, or **⏻** to stop the server entirely (both fully release
   GPU/VRAM).

---

## Optional: Google Calendar

1. In the [Google Cloud Console](https://console.cloud.google.com): create a project,
   enable the **Google Calendar API**.
2. **APIs & Services → Credentials → Create Credentials → OAuth client ID →
   Desktop application**, then download the JSON.
3. Launch the app and load that `credentials.json` from the setup screen. A browser opens
   once to authorize; the token is cached in `~/.daily-scheduler/`.

The app works fully offline without this — Google Calendar just adds read-only event
overlays.

---

## Building the executable

```bat
pip install pyinstaller
py -m PyInstaller --noconfirm --onefile --windowed --name DailyScheduler --collect-all PySide6 app.py
```

The result lands in `dist/DailyScheduler.exe` (this project builds to `dist_exe/`).

---

## Data & storage

All local, under `~/.daily-scheduler/`:

| File | Purpose |
|------|---------|
| `activities.json` | your scheduled blocks |
| `credentials.json` / `token.json` | Google OAuth (only if you connect Calendar) |

## Tech

Python · PySide6 (Qt6) · Ollama (local LLM, tool-calling) · Google Calendar API
