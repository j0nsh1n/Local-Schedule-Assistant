# Daily Scheduler

A native Windows desktop **daily planner** with a **local, private AI assistant** that can
read and edit your schedule — all running on your own machine. No cloud, no accounts, no
telemetry: the UI is native Qt, and the AI runs locally through [Ollama](https://ollama.com).

Built with Python + PySide6 (Qt6). MIT licensed.
*(This repository is named `Local-Schedule-Assistant`; the app itself is **Daily Scheduler**.)*

> **Private by design** — your schedule never leaves your computer. The AI assistant and
> all of your data are fully local. Google Calendar integration is optional and read-only.

---

## Features

- **Day / Month / Year views** — a clean timeline of your full 24-hour day. Navigate with
  the `‹` / `›` arrows and the **Today** button.
- **Direct editing** — drag on empty timeline to create a block, drag a block to move it,
  drag its edges to resize, click (or right-click → Edit) to change its title/type/time,
  right-click → Delete to remove it. Eight activity types (Assignments, Projects, Study,
  Extracurriculars, Anime/Gaming, Exercise, Meals, Sleep), each with its own color.
- **Local AI assistant** — three modes (*Chat*, *Plan*, *Analyze*) backed by a local LLM
  that edits your schedule with real tools:
  - *"Add a study block from 2 to 4pm"*
  - *"Plan my day: 3 hours of homework in 30-minute chunks, lunch at 1, workout at 4"*
  - *"My dentist appointment moved to 2pm — make room for it without deleting anything"*
  - *"I'm running 30 minutes late — push the rest of my day"*
  - *"Spread 4 hours of exam prep across the days before Friday"*
  - *"How much sleep and study did I get this week?"*

  Tools include `add_block`, `move_block`, `delete_block`, `split_block`, `add_recurring`,
  `shift_blocks`, `copy_day`, `clear_day`, `schedule_tasks`, `plan_day`, `make_room`,
  `reflow_from_now`, `plan_for_deadline`, `find_free_time`, `week_summary`, and more. The
  assistant knows the current date/time, plans around your (read-only) calendar events,
  verifies its own work, and the app enforces a conflict-free schedule.
- **Desktop notifications** — an alert when a block starts, with an optional lead time
  ("5 minutes before") and a **Do-Not-Disturb override** that draws its own always-on-top
  popup so reminders break through Windows Focus Assist.
- **Lives in the tray** — closing the window keeps reminders running; the tray menu has
  Open / notification toggles / Test / Settings / Quit.
- **Start with Windows** — optional. At sign-in the app opens its window after a short
  settle delay (configurable).
- **Two themes** — *Nocturne* (dark) and *Slate* (light), switchable in Settings.
- **Optional Google Calendar** — overlays your real events (read-only); the AI plans
  around them and never touches them.

---

## Requirements

| Component | Requirement |
|---|---|
| **Operating system** | Windows 10/11, or Linux (v3.0.0+, run from source — see the Linux notes below; macOS may work but is untested) |
| **The planner itself** | Any modern PC — the app is lightweight |
| **AI assistant** *(optional)* | [Ollama](https://ollama.com) + ideally a GPU with **8–16 GB VRAM** (see the model guide below). CPU-only works but replies are slow. |
| **Google Calendar** *(optional)* | A free Google Cloud project (steps below) |

The planner works fully offline with **neither** Ollama **nor** Google Calendar set up.

---

## Install

### Option A — prebuilt executable (easiest, no Python)

1. Download `DailyScheduler.exe` from the [latest release](../../releases/latest).
2. Run it. If Windows SmartScreen shows *"Windows protected your PC"*, click
   **More info → Run anyway** (the exe is unsigned, not malicious — you can audit
   [`app.py`](app.py); the whole app is one file).
3. That's it. Your data lives in `~/.daily-scheduler/` (i.e. `C:\Users\<you>\.daily-scheduler\`).

### Option B — run from source

Requires **Python 3.10+**.

```bat
git clone https://github.com/j0nsh1n/Local-Schedule-Assistant.git
cd Local-Schedule-Assistant
run.bat
```

`run.bat` (Windows) / `./run.sh` (Linux) installs the dependencies and launches the
app. Or manually:

```bat
pip install -r requirements.txt
python app.py
```

### Linux notes (v3.0.0+)

- **Run at login** uses a standard XDG autostart entry
  (`~/.config/autostart/daily-scheduler.desktop`) — toggle it from the tray menu
  ("Start at login") or your desktop's autostart settings.
- **Tray icon:** KDE Plasma works out of the box. Vanilla **GNOME needs the
  [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/)**
  or the icon won't show; the app still works — closing the window without a tray
  just keeps it running, relaunch to surface it.
- **Alert popups on Wayland:** the always-on-top reminder popup may appear
  center-screen instead of the bottom-right corner (Wayland doesn't let apps
  position their own windows). Cosmetic only. On KDE you can restore corner
  placement with a window rule (System Settings → Window Management → Window
  Rules) matching the exact window title `Daily Scheduler Alert`: set
  *Position* (Apply initially) and *Keep above* (Force).
- **Ollama:** install the Linux build from [ollama.com](https://ollama.com/download);
  AMD GPUs use the bundled ROCm (RDNA2+ supported out of the box).

---

## Setting up the AI assistant (Ollama)

The AI is optional — skip this section and you still have a full-featured planner.

[Ollama](https://ollama.com) is a free, open-source runtime that runs large language
models entirely on your own machine. Nothing you type ever leaves your PC.

**1. Install Ollama** — download the Windows installer from
[ollama.com/download](https://ollama.com/download) and run it. Verify it works by opening
a terminal (press <kbd>Win</kbd>, type *cmd*, Enter) and running:

```bat
ollama --version
```

**2. Pull a model** — pick one that fits your GPU's VRAM (check it in Task Manager →
Performance → GPU → *Dedicated GPU memory*), then run the command in a terminal:

| Your GPU VRAM | Suggested model | Download |
|---|---|---|
| 12–16 GB | `ollama pull qwen3:14b` ← **recommended**; or `qwen2.5:14b` / `deepseek-r1:14b` / `gpt-oss:20b` | ~9–13 GB |
| 16 GB (tight fit) | `ollama pull mistral-small3.1:24b` — excellent tool-calling | ~14 GB |
| ~8 GB | `ollama pull qwen3:8b` — smaller; tool-calling is less reliable | ~5 GB |
| No dedicated GPU | a small model on CPU (e.g. `qwen3:8b`) — expect slow replies | ~5 GB |

This app is *tool-heavy* (the model edits your schedule by calling functions), so models
with strong **tool-calling** matter more than raw size. The in-app picker recommends a
curated, verified set — `qwen3:14b`, `gpt-oss:20b`, `deepseek-r1:14b`, `qwen2.5:14b`,
`gemma4`, `glm-4.7-flash`, `mistral-small3.1:24b` — and also lists every model you've
already pulled. The app tailors its prompting to each recommended model and automatically
hides reasoning models' `<think>` output.

**3. Start it from inside the app** — open the AI panel (the **AI** button in the header)
and:

- Press **▶** *(start)* to launch the Ollama server. The status dot turns green when
  connected.
- Pick your model from the **dropdown** in the panel header.
- Chat. The first reply after starting is slower while the model loads into GPU memory.

When you're done, **⏏** *(unload)* frees the model from memory and **⏻** *(stop)* shuts the
server down — both fully release GPU/VRAM, so Ollama uses zero resources until you press ▶
again. (Prefer it automatic? Settings → **General** can start the server with the app.)

> **Tip:** the Ollama installer also adds its own small tray app that starts with Windows.
> Daily Scheduler doesn't need it running — the ▶ button starts the server on demand — so
> you can quit it / remove it from Startup apps if you want zero idle usage.

---

## Optional: Google Calendar

Adds a read-only overlay of your real calendar events; the AI plans around them. Because
this uses *your own* free Google Cloud project, your calendar data flows only between your
PC and Google — there's no third-party server.

1. Go to the [Google Cloud Console](https://console.cloud.google.com) and create a project
   (any name).
2. **APIs & Services → Library** → search **Google Calendar API** → **Enable**.
3. **APIs & Services → OAuth consent screen** (newer Console: **Google Auth Platform →
   Branding/Audience**) → choose **External** → fill in the app name and your email → save.
   Then under **Audience → Test users**, **add your own Google account** — without this,
   sign-in is refused while the app is in "Testing" mode.
4. **APIs & Services → Credentials → + Create Credentials → OAuth client ID** → application
   type **Desktop app** → **Download JSON**.
5. Launch Daily Scheduler and load that JSON from the setup screen
   (**Load credentials.json…**). A browser opens once to authorize.
6. You'll see **"Google hasn't verified this app"** — that's expected (you are the
   developer of this OAuth client). Click **Advanced → Go to … (unsafe) → Continue**.

The token is cached in `~/.daily-scheduler/token.json` (read-only calendar scope). The app
works fully offline without any of this.

---

## Everyday use

- **Close ≠ quit.** Closing the window hides the app to the system tray so reminders keep
  firing. Really quit via the tray icon → **Quit**.
- **Launching it again** just brings the running window to the front (single instance).
- **Start with Windows** (tray menu or Settings) adds a Startup shortcut. At sign-in the
  app waits a few seconds for the system to settle, then opens its window
  (`startup_delay_sec` in settings, default 5; set `0` for instant).
- **Notifications** fire once per block start — set a lead time in Settings to be warned
  *N* minutes early. With **Override Do Not Disturb** on (default), alerts use the app's
  own popup + sound so Focus Assist can't swallow them.

---

## Settings

Open **Settings** from the header ⚙ or the tray menu. Everything persists in
`~/.daily-scheduler/settings.json`:

- **General** — theme (dark *Nocturne* / light *Slate*; applied on next launch), Start
  with Windows, and whether to auto-start the Ollama server when the app launches.
- **Notifications** — block-start alerts on/off, lead time, Do-Not-Disturb override.
- **AI Assistant** — model, temperature, context window, and default planning hours.
- **Data** — open the data folder or export a backup of your schedule.

---

## Data & storage

Everything lives in `~/.daily-scheduler/` — plain JSON you can back up or inspect:

| File | Purpose |
|------|---------|
| `activities.json` | your scheduled blocks |
| `settings.json` | app settings |
| `credentials.json` / `token.json` | Google OAuth (only if you connect Calendar) |
| `startup.log` | launch diagnostics (timestamp, pid, launch flags — never schedule data) |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| SmartScreen blocks the exe | **More info → Run anyway** (unsigned binary) |
| Nothing appears for a few seconds at Windows sign-in | Normal — the app waits `startup_delay_sec` (default 5 s) before opening. Set it to `0` in `settings.json` for instant. |
| "X" closed the window and it's "gone" | It's in the system tray — click the tray icon, or just launch the app again to surface it. |
| AI panel says **Not running** | Press **▶** (requires Ollama installed). |
| First AI reply is very slow | The model is loading into GPU memory — later replies are fast. |
| AI replies are slow *every* time | The model doesn't fit your VRAM — pull a smaller one (see the table above). |
| "Google hasn't verified this app" during Calendar sign-in | Expected — **Advanced → Continue**. Make sure your account is added as a **Test user** (Calendar step 3). |
| A meeting isn't visible to the AI yet | Calendar sync is per-month and can lag a moment — press **↺** (Refresh). |

---

## Building the executable yourself

```bat
pip install pyinstaller
py -m PyInstaller --noconfirm --onefile --windowed --name DailyScheduler --collect-all PySide6 app.py
```

The result lands in `dist\DailyScheduler.exe` (~270 MB — it bundles Python and Qt).

---

## Tech

Python · PySide6 (Qt6) · [Ollama](https://ollama.com) (local LLM, tool-calling) ·
Google Calendar API (optional, read-only) · single-file app (`app.py`)

## License

[MIT](LICENSE) © Jonathan Shin
