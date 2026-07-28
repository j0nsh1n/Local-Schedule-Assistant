# Architecture

A native desktop day-planner with a local, private AI assistant.
Pure **Python + PySide6 (Qt6)** — no browser engine, no web view, no QML.

New here? Read this file, then `core.py`. Every module opens with a docstring
listing its contents, so you can orient inside a file without scrolling it.

---

## The module map

Arrows point the way imports flow. Nothing below imports anything above it, so
the graph stays acyclic.

```text
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
| `platform_utils.py` | Run-at-login, alert sounds, desktop notifications, update check — all OS branching. | ~365 |
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

- **Placement avoids overlaps** — with one documented exception. Everything that
  routes through `sequentialize()` / `find_free_placement()` passes the day's
  calendar events as `blocked=`, so editable blocks are pushed off read-only
  meetings rather than landing on them.
  **Exception:** `add_recurring` stamps each occurrence at the requested time
  and only *reports* the days that collided — it does not reposition, and it
  does not consult calendar events (see Known gaps).
- **Calendar events are obstacles, not data.** Nothing writes to Google.
  All-day events are marked `allDay` and never consume free time.
- **Nothing is lost silently, but "not deleted" isn't "not lost."**
  `replace_day` and `clear_*` delete outright. Separately, any tool that
  re-lays a day (`shift_blocks`, `plan_day`, `make_room`, `reflow_from_now`,
  `copy_day`) can push a block past 24:00, and `sequentialize()` **drops**
  what no longer fits — the count comes back as `n_dropped` and every one of
  those tools reports it in its result string. Every mutating turn takes an
  undo snapshot first, so ↶ Undo / `Ctrl+Z` restore the whole thing.
- **Alerts fire exactly once**, even with two app instances running — the
  claim is an atomic `O_CREAT|O_EXCL` marker file, not an in-memory set.
- **How an alert is drawn depends on the OS** (`MainWindow._alert`). On Linux
  it goes to the desktop notification daemon over D-Bus, because Wayland does
  not let a client place its own window — the daemon puts it in whatever corner
  the user configured, and "override DND" maps to *critical* urgency. Windows
  (and any Linux box where that call fails) falls back to the hand-drawn
  `AlertPopup`, which pins itself to the corner of the screen the main window
  is on. Never assume the custom popup is what the user sees.
- **Data is local and plain JSON**, under `~/.daily-scheduler/`, with three
  recovery layers: `.bak` (one save back), 14 dated dailies, and in-session undo.

---

## Known gaps

Real, currently-accepted limitations — listed so nobody has to rediscover them.

- **`add_recurring` can create overlaps.** It places every occurrence at the
  literal requested time, reporting collisions instead of avoiding them, and it
  ignores calendar events entirely. That's defensible for its main use (a
  weekly class belongs at its real time), but it is the one placement path that
  can leave the day double-booked. Routing it through `sequentialize()` with
  `blocked=_cal_intervals(ds)` — per occurrence, per day — would close it.
- **Recurring blocks are stamped copies, not a rule.** Changing a class time
  means editing every occurrence.
- **The AI only sees the viewed day plus a 7-day calendar preview**, so it can
  reason about upcoming events but not about blocks you scheduled next month.

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
