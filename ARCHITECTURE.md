# Architecture

A native desktop day-planner with a local, private AI assistant.
Pure **Python + PySide6 (Qt6)** — no browser engine, no web view, no QML.

New here? Read this file, then `core.py`. Every module opens with a docstring
listing its contents, so you can orient inside a file without scrolling it.

---

## The module map

Arrows point the way imports flow. Nothing below imports anything above it, so
the graph stays acyclic.

```
                         app.py            entry point: main(), single-instance guard
                            │
                       mainwindow.py       owns app state, wires everything together
                            │
        ┌───────────┬───────┴───────┬────────────┐
        │           │               │            │
    views.py    dialogs.py     aipanel.py    ai_tools.py     UI + AI turn loop + tools
        │           │               │            │
        └───────────┴───────┬───────┴────────────┘
                            │
        ┌──────────┬────────┼─────────┬──────────────┐
        │          │        │         │              │
    theme.py    gcal.py   ai.py   platform_utils.py  │           services
        └──────────┴────────┴─────────┴──────────────┘
                            │
                         core.py            constants, storage, settings, scheduling math
```

| Module | Owns | Size |
|---|---|---|
| `core.py` | Data model, storage, backups, settings, **the scheduling math**. Qt-free. | ~670 |
| `theme.py` | Two themes, the `C_*` colour globals, block/chip paint recipe. | ~220 |
| `gcal.py` | Google auth + fetch threads, event normalisation. Read-only data. | ~215 |
| `ai.py` | Ollama process control, threads, tool *schemas*, per-model prompts. | ~1,215 |
| `ai_tools.py` | What the tools actually *do* to the schedule (`AIToolsMixin`). | ~990 |
| `platform_utils.py` | Run-at-login, alert sounds, update check — all OS branching. | ~300 |
| `views.py` | Day / Week / Month / Year / sidebar, all custom-painted. | ~950 |
| `dialogs.py` | Add-activity, setup, alert popup, settings. | ~810 |
| `aipanel.py` | Chat UI and the turn loop. | ~895 |
| `mainwindow.py` | Application state + wiring. Inherits `AIToolsMixin`. | ~1,215 |
| `app.py` | Entry point only. | ~240 |

---

## Two rules that are easy to break

**1. Reach mutable globals through their module.**

```python
import theme;  theme.C_BG        # correct
from theme import C_BG           # WRONG — silently stops updating
```

`apply_theme()` *rebinds* the `C_*` globals, and the tests redirect `core`'s path
globals (`DATA_FILE`, `BACKUP_DIR`, …) to a temp directory. A `from x import Y`
captures the value once at import time, so a theme switch stops propagating and
tests start writing to the real data store. Stable things — pure functions,
widget classes, constants that never change — are fine to `from`-import.

`tests/test_module_boundaries.py` fails the build if this rule is broken, and
names the offending file.

**2. `core.py` must not import Qt.**

It's the one module testable without a display. `tests/test_core_pure.py` runs the
whole scheduling layer in ~0.03 s with no PySide6 installed, and CI runs it as a
separate job that deliberately skips `requirements.txt` — so importing Qt into
`core` breaks the build.

---

## Where do I add…?

| I want to… | Go to |
|---|---|
| add an activity category | `core.ACTIVITY_TYPES` — the pickers, tool enums and AI prompt all generate from it |
| add an AI tool | schema in `ai.AI_TOOLS`, behaviour in `ai_tools._ai_execute` |
| change how the model is steered | `ai.model_guidance()` (per family) or `aipanel._sys_prompt()` |
| add a setting | `core.DEFAULT_SETTINGS`, then a row in `dialogs.SettingsDialog` |
| change scheduling/placement | `core.py` — `sequentialize`, `find_free_placement`, `_free_slots`, `_earliest_fit` |
| change how a view looks | `views.py` (`theme.py` if it's a colour or the block recipe) |
| add OS-specific behaviour | `platform_utils.py` — keep the branching out of widgets |

After **any** schedule mutation call `MainWindow._refresh_view()`; it is the
single repaint path and also updates the Now/Next line.

---

## Invariants worth knowing

- **Blocks never overlap.** Placement runs through `sequentialize()` /
  `find_free_placement()` with the day's calendar events passed as `blocked=`,
  so editable blocks are pushed off read-only meetings rather than landing on
  them.
- **Calendar events are obstacles, not data.** Nothing writes to Google.
  All-day events are marked `allDay` and never consume free time.
- **The AI can't silently destroy a day.** Only `replace_day` and `clear_*`
  delete; every mutating turn takes an undo snapshot first, and ↶ Undo /
  `Ctrl+Z` restore it.
- **Alerts fire exactly once**, even with two app instances running — the
  claim is an atomic `O_CREAT|O_EXCL` marker file, not an in-memory set.
- **Data is local and plain JSON**, under `~/.daily-scheduler/`, with three
  recovery layers: `.bak` (one save back), 14 dated dailies, and in-session undo.

---

## Testing

```bash
python3 tests/test_core_pure.py            # no Qt, no display, ~0.03 s
QT_QPA_PLATFORM=offscreen python3 tests/test_<name>.py
```

Suites are plain scripts that print `RESULT: PASS` and exit non-zero on failure —
no pytest. CI runs all of them offscreen on every PR.

**Privacy rule, non-negotiable:** this app holds a real teenager's schedule.
Tests use synthetic data only and must sandbox `HOME` **before** importing `core`
(it creates `DATA_DIR` at import time). Never read or print the real store.
