# context.md — Daily Scheduler

## Current State
- Branch: `main`. App version **4.6.3** (cloud model picker refresh).
- Prior release **v4.6.2** (notifications). **Python 3.14** + pyright.
- Notifications: FreeDesktop native path. Deps: `>=` floors. No Dependabot.

## Repo Landmarks
- Entry: `app.py`, `run.sh` / `run.bat`
- Core/data: `core.py` (Qt-free; owns `__version__`, paths, settings, math)
- UI: `mainwindow.py`, `views.py`, `dialogs.py`, `aipanel.py`, `theme.py`
- AI: `ai.py`, `ai_tools.py`
- OS: `platform_utils.py`
- Calendar: `gcal.py`
- Types: `pyrightconfig.json`
- Docs: `README.md`, `ARCHITECTURE.md`, `spec.md`, `roadmap.md`, this file,
  `CHANGELOG.md`, `agents.md`
- Tests: `tests/test_*.py` (synthetic; excluded from pyright include)
- CI: `.github/workflows/ci.yml`, `codeql.yml`, release-linux/windows
- Data at runtime: `~/.daily-scheduler/`

## Domain Model
- **Block**: id, date, startMin, endMin, type, color, title — `activities.json`.
- **Activity type**: from `core.ACTIVITY_TYPES` (~19).
- **Settings**: theme, model, llm_*, notify_*, plan window, ollama_*, layout.
- **Calendar event**: Google read-only overlay / blocked intervals.
- **Chat**: `chat.json`. **Backups**: `.bak` + dated files.
- MainWindow state → views; AI tools mutate → `_refresh_view()`.

```
  settings.json ──► MainWindow / AI clients
  activities.json ◄► load/save / AI tools / views
  Google API ──(read)──► overlay events
  Ollama or cloud API ◄── prompts + tool calls
```

## Non-Obvious Decisions
- Python **3.14 only** for supported source builds (not 3.10–3.12 matrix).
- Pyright basic: suppress `reportAttributeAccessIssue` and optional-member
  family (PySide6 enum/optional stub noise); tests not in include list.
  Prefer real fixes over more suppresses.
- `core.py` Qt-free; attribute access for rebindable globals.
- Custom test scripts (not pytest). Deps `>=` floors. No Dependabot.
- Linux alerts: FreeDesktop first; Qt popup fallback.
- Version jump 4.4 → 4.6 (no 4.5.x).

## Session Handoff
- **Date:** 2026-08-04
- **Branch:** `main`
- **Done:** PR #47 merged (model seeds); bump to 4.6.3 and cut release.
- **Next:** Optional: fetch cloud models live from provider APIs.
