# roadmap.md — Daily Scheduler (Local-Schedule-Assistant)

Note: "Complete when" conditions are verified locally (tests pass, feature
works) and via PR review. One phase may span several small PRs.

## Phase 1 — Governance & hygiene
- Tasks:
  - Tracked `agents.md`, filled `spec.md`, `roadmap.md`, `context.md`,
    `CHANGELOG.md` (keepachangelog).
  - Root `context.md` trackable; removed legacy `daily-scheduler-handoff/`
    archive; uppercase `CONTEXT.md` remains gitignored if present locally.
  - Live CI kept project-specific; CodeQL workflow added under
    `.github/workflows/`; **no Dependabot** (avoids spammy remote PRs).
  - Align agent workflow with agents.md + this repo’s spec.
- Complete when: governance files exist and are committed when human asks.
- Status: [x] 2026-07-31 — files on `chore/governance-3.14-pyright`

## Phase 2 — v4.6.2 notifications
- Tasks:
  - Linux: FreeDesktop Notifications first; custom `AlertPopup` fallback.
  - Multi-monitor geometry for custom fallback on preferred/primary screen.
  - README matches native toast behavior.
  - Tests for notify helper / geometry where present.
- Complete when: human confirms native system notifications work as intended
  on target Linux.
- Status: [x] 2026-07-31 — confirmed working natively as intended

## Phase 3 — Release v4.6.2
- Tasks:
  - CHANGELOG finalized for 4.6.2; tag `v4.6.2`; release workflows/scripts
    with human go-ahead (this session).
  - Attach win + linux (+ AppImage if built) assets and checksums.
- Complete when: GitHub release `v4.6.2` published with assets; app version
  on main matches tag.
- Status: [~] 2026-07-31 — cutting release

## Phase 4 — Dependency & CI hardening
- Tasks:
  - Pin policy: keep `>=` floors in `requirements.txt` (decided).
  - Python **3.14** everywhere (CI, release, launchers, `.python-version`).
  - Pyright: config + CI + 0-error gate; Qt stub noise suppressed deliberately.
  - No Dependabot unless explicitly requested later.
- Complete when: pyright green locally/CI config present; 3.14 pin in spec.
- Status: [x] 2026-07-31 (local pyright 0 errors; CI updated; not pushed yet)

## Backlog (unscheduled)
- Recurring blocks as rules (not only stamped copies).
- Route remaining placement paths through sequentialize + calendar blocked
  intervals where appropriate.
- AI multi-week / beyond 7-day calendar preview awareness.
- macOS support (untested).
- Exact dependency lockfile only if release reproducibility becomes painful.
- Migrate test runner to pytest only if benefits outweigh custom RESULT: PASS
  scripts (spec change required).
- Tighten pyright (optional-member / attribute reports; test includes;
  refactor `ai_tools` complexity warning).
