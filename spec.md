# spec.md — Daily Scheduler (Local-Schedule-Assistant)

## Problem

People need a full-day planner that stays private on their own machine, with
optional local (or user-keyed) AI that can actually edit the schedule—not a
web SaaS calendar and not a chatbot that only suggests text. Daily Scheduler
is a native desktop day planner (Qt) whose AI tools mutate a local conflict-aware
schedule, with optional read-only Google Calendar overlay and optional cloud LLM
backends when the user supplies a key.

## Intended Users

Solo local use: primarily a household/high-school schedule on a personal Windows
or Linux PC. Not a multi-tenant or hosted product. One interactive user per
machine install; data under that user’s home directory.

## Required Behavior

- Present Day / Week / Month / Year views of a 24-hour day and allow create /
  move / resize / edit / delete of activity blocks with typed categories.
- Persist schedule and settings under `~/.daily-scheduler/` (activities,
  backups, settings, chat transcript, logs). Survive restarts.
- Optional AI assistant (Chat / Plan / Analyze) that can call tools to mutate
  the schedule (`add_block`, `move_block`, `plan_day` / `plan_days`, etc.) and
  verify results; default backend is local Ollama.
- Optional cloud LLM providers (OpenAI, Anthropic, OpenAI-compatible) only when
  the user configures provider + API key; local Ollama remains the default.
- Optional Google Calendar overlay is **read-only**; AI plans around events and
  must not write to Google.
- Desktop alerts when blocks start (lead time, sound, DND override popup);
  on Linux prefer FreeDesktop Notifications when available so the compositor
  can place toasts.
- Tray residency: closing the main window keeps reminders running until Quit.
- Optional start-at-login (Windows Startup / Linux XDG autostart).
- Themes: Nocturne (dark) and Slate (light).
- Single-instance guard: a second launch focuses the existing process.
- Edge cases: empty schedule works offline with no Ollama/Google; bad settings
  fall back to defaults; network failures for update-check / cloud LLM / Google
  fail soft without corrupting local data; tests and CI use synthetic data only.

## User Experience

Native desktop GUI (PySide6 / Qt6)—no embedded browser, no QML.

```bash
# From source (Linux)
./run.sh
# or
pip install -r requirements.txt && python app.py

# From source (Windows)
run.bat
```

Prebuilt assets: GitHub Releases (`DailyScheduler-win64.zip`,
`DailyScheduler-linux-x86_64.zip`, optional AppImage). App identity on Linux
desktop: `./install-launcher.sh` installs `daily-scheduler` + XDG entry.

Deliberate choices: pure Python modules (not a web stack); `core.py` stays
Qt-free for fast pure tests; custom offscreen test scripts (not pytest).

## Architecture

- Language/runtime: **Python 3.14** (PINNED). Source of truth: this section,
  `.python-version`, and CI (`python-version: "3.14"`). Launchers prefer 3.14
  (`run.sh` / `run.bat`).
- Frameworks / deps (`requirements.txt`, floors not lockfile pins):
  - `PySide6>=6.7.0` (local often 6.11.x)
  - `requests>=2.32.0`
  - `google-auth-oauthlib>=1.2.0`
  - `google-api-python-client>=2.130.0`
- Storage: JSON files under `~/.daily-scheduler/` (no SQL). Key paths owned by
  `core.py` (`DATA_FILE`, `SETTINGS_FILE`, backups, chat, logs).
- Major components (import DAG; see `ARCHITECTURE.md`):
  - `app.py` — entry, single-instance, `main()`
  - `mainwindow.py` — app state, wiring, tray; uses `AIToolsMixin`
  - `views.py` — Day / Week / Month / Year / sidebar UI
  - `dialogs.py` — settings, edit activity, alert popup, setup
  - `aipanel.py` — chat UI + turn loop
  - `ai_tools.py` — tool execution against the schedule
  - `ai.py` — Ollama/cloud clients, tool schemas, prompts, model profiles
  - `gcal.py` — Google auth + fetch (read-only)
  - `platform_utils.py` — login item, sounds, desktop notify, update check
  - `theme.py` — themes and `C_*` paint globals
  - `core.py` — version, paths, settings, scheduling math (no Qt)
  - `tests/` — synthetic suites only
- External APIs/services:
  - Ollama (local HTTP) — optional
  - OpenAI / Anthropic / OpenAI-compatible HTTP APIs — optional, user key
  - Google Calendar API — optional OAuth, read-only
  - GitHub Releases API — optional update check (`core.GITHUB_REPO`)

Version string: `core.__version__` / `core.APP_VERSION` (currently 4.6.2 on
branch work; release tags may lag until cut).

## Security & Privacy

- No secrets in git. Google OAuth client files and tokens live under
  `~/.daily-scheduler/` (also gitignored names for accidental copies).
  LLM API keys are stored only in local `settings.json` (not committed);
  UI/logs use `mask_api_key`; never log full keys or schedule contents in
  diagnostics meant for sharing.
- Real user schedule data (`activities.json`, calendar titles, chat that
  embeds schedule context) is private. Agents and tests must use **synthetic
  data** and sandbox `HOME` / `DATA_FILE` before exercising storage—never read
  or print the real store for debugging or chat replies.
- Cloud LLM mode is opt-in: when enabled, prompts and schedule context leave
  the machine to the chosen provider. Default remains local Ollama.
- Dependencies: floors in `requirements.txt` (`>=` policy; no lockfile
  required). **No Dependabot** — do not add `.github/dependabot.yml` unless
  the human explicitly requests it (historical issue: noisy remote PRs).
- CodeQL: `.github/workflows/codeql.yml` scans Python on push/PR + weekly.
- GPL-3.0-or-later (`LICENSE`).

## Validation & Tooling

Match live CI (`.github/workflows/ci.yml`) unless this section is updated with
approval:

- Lint: `ruff check *.py --select E9,F63,F7,F82` — must pass.
- Syntax: `python -m py_compile *.py` — must pass.
- Types: `pyright` — must pass (0 errors). Config: `pyrightconfig.json`
  (`typeCheckingMode: basic`, app modules only, tests excluded).
  Qt stub noise is suppressed (`reportAttributeAccessIssue` /
  optional-member reports off); remaining optional/Qt debt may be tightened
  later. Prefer fixing real errors over widening suppresses. Install
  `pyright` (+ `requirements.txt` for import resolution) in the env.
- Tests (must pass locally before merge; same idea as CI):
  - `python tests/test_core_pure.py`  # no Qt; must not need requirements.txt
  - `QT_QPA_PLATFORM=offscreen python tests/test_<name>.py` for changed areas,
    or run all `tests/test_*.py` under offscreen as CI does.
- Suites are plain scripts that print `RESULT: PASS` / `N/N passed`—not pytest.
  Qt teardown may exit non-zero after a success marker; CI treats success
  markers as pass unless `[FAIL]` appears.
- Module rules enforced by tests: no `from theme import C_*` (etc.) for
  rebindable globals; `core.py` must not import Qt
  (`tests/test_module_boundaries.py`, `tests/test_core_pure.py`).

## Acceptance Criteria

- [ ] User can run the app from source on Windows or Linux with **Python 3.14**
      and edit a day without any AI or Google setup.
- [ ] With Ollama (or a configured cloud provider), AI tools can add/move
      blocks and the UI refreshes without overlapping placement for normal
      placement paths.
- [ ] Validation commands above exit successfully (lint, syntax, **pyright**,
      core tests); full offscreen suite has no `[FAIL]` markers.
- [ ] CHANGELOG.md updated for user-visible changes.
- [ ] No real schedule data committed or pasted into issues/PRs/agent logs.
- [ ] `core.__version__` bumped when cutting a release; release assets match tag.
