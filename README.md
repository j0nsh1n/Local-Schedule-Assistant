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
  - *"Shift everything 2 hours later"* · *"Copy my schedule to 6/14"*
  - *"Split my afternoon into 30-minute study blocks with breaks"*
  - *"I'm running 30 minutes late — push the rest of my day"*
  - *"Plan 4 hours of exam study across the days before Friday"*
  - *"How much sleep and study did I get this week?"*
  - It calls real tools (`add_block`, `add_recurring`, `move_block`, `delete_block`,
    `clear_range`, `clear_day`, `shift_blocks`, `copy_day`, `split_block`, `schedule_tasks`,
    `find_free_time`, `reflow_from_now`, `plan_for_deadline`, `week_summary`, `replace_day`,
    `list_blocks`), knows the current date/time, verifies its own work, and the app enforces a
    conflict-free schedule.
- **Two themes** — *Nocturne* (dark) and *Slate* (light): a clean, sharp-cornered, editorial
  look. Switch in Settings.
- **Settings** — a central dialog (header ⚙ or tray → *Settings…*) for theme, model,
  notifications + lead time, Do-Not-Disturb override, default planning hours, and whether to
  auto-start the AI server. All settings persist between launches.
- **Desktop notifications** — an alert when a block starts, with an optional lead time and a
  Do-Not-Disturb override that breaks through Focus Assist.
- **Start with Windows** — optional; opens quietly into the tray (the AI server is *not*
  started automatically — only when you press ▶, or enable auto-start in Settings).
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
2. Pull one or more models. The picker recommends models that fit a 16 GB GPU and are strong
   at tool-calling (this app is tool-heavy):
   ```bat
   ollama pull qwen3:14b        # recommended — best tool-calling stability
   ollama pull gpt-oss:20b      # fast, strong reasoning
   ollama pull deepseek-r1:14b  # reasoning model (chain-of-thought)
   ollama pull qwen2.5:14b      # default / fallback
   ```
   Choose a model from the **dropdown** in the AI panel header (or in **Settings → AI**). It
   lists everything `ollama list` reports plus the recommended set. The app tailors its prompt
   to each model's strengths and automatically hides reasoning models' `<think>` output.
3. In the app, open the AI panel and press **▶** to start the local server. Use **⏏** to
   unload the model from memory, or **⏻** to stop the server entirely (both fully release
   GPU/VRAM).

---

## Settings & themes

Open **Settings** from the header ⚙ button or the tray menu (*Settings…*). Everything is saved
to `~/.daily-scheduler/settings.json` and restored on the next launch:

- **General** — theme (*Nocturne* dark / *Slate* light), Start with Windows, and whether to
  auto-start the Ollama server on launch.
- **Notifications** — turn block-start alerts on/off, set a lead time (alert *N* minutes
  early), and toggle the Do-Not-Disturb override.
- **AI** — model, temperature, context window, and the default planning hours the assistant
  schedules within.
- **Data** — open the data folder or export your schedule.

> Changing the theme takes effect the next time you open the app.

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
| `settings.json` | app settings (theme, model, notifications, planning hours, …) |
| `credentials.json` / `token.json` | Google OAuth (only if you connect Calendar) |

## Tech

Python · PySide6 (Qt6) · Ollama (local LLM, tool-calling) · Google Calendar API
