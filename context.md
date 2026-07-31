# context.md — Daily Scheduler

## Current State
- Branch: `chore/governance-3.14-pyright` (from main after PR #45 merge).
  App version **4.6.2**; last published release tag **v4.6.1** (tag cut in
  progress for v4.6.2).
- **Python 3.14** pinned; **pyright** adopted (0 errors local).
- Notifications: on main via PR #45; human confirmed native toasts work.
- Cloud LLM: leave as-is. Deps: `>=` floors. No Dependabot.

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
- **Date:** 2026-07-31
- **Branch:** `fix/v4.6.2-notifications` → merge/release path for v4.6.2
- **Done:** Governance, Python 3.14, pyright/CodeQL, notify fixes, changelog
  finalized for 4.6.2; commit/PR/release in progress per human request.
- **Next:** After release lands on main, prune branch; start next task from
  roadmap backlog or human ask.
