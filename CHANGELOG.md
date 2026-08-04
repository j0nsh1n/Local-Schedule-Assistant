# Changelog

All notable user-visible changes to Daily Scheduler are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/).
Versions match `core.__version__` / Git tags (`vX.Y.Z`).

## [Unreleased]

## [4.6.3] - 2026-08-04

### Changed
- Cloud LLM model picker seeds updated to current OpenAI (GPT-5.6 family)
  and Anthropic (Claude Sonnet/Opus/Fable 5, Haiku 4.5) API ids; README
  examples match.

## [4.6.2] - 2026-07-31

### Fixed
- **Linux reminders** use FreeDesktop Notifications (`gdbus` →
  `org.freedesktop.Notifications`) so Plasma/GNOME place system toasts
  correctly (corner), instead of a Wayland-centered custom window.
- Desktop notify runs on a **worker thread** so a slow session bus cannot
  freeze the UI (~10 s worst case before).
- **Multi-monitor** custom popup fallback: target the screen the main window
  (or cursor) is on; corner geometry works for screens that do not start at
  (0,0). Dropped `Qt.Tool` so KWin keep-above/placement works on the fallback.
- README Linux notes match native toast primary path + Qt fallback only.

### Added
- Tests: `tests/test_desktop_notification.py`, `tests/test_alert_position.py`.

## [4.6.1] - 2026-07-26

### Added
- Multi-day planning tools / date target expansion for the AI assistant.
- Optional cloud LLM providers (OpenAI, Anthropic, OpenAI-compatible) with
  user-supplied API key in settings.
- Schedule editing UX: copy/paste/duplicate/clear day and related block
  operations; improved selection vs edit click behavior.

### Changed
- Version and packaging pipeline updates for Windows + Linux release assets.

## [4.4.0] - 2026-07

### Changed
- Debloat / navigation structure cleanup after modular split.

## [4.3.x] - 2026-07

### Added
- AI tools extracted to mixin; pure core tests and module-boundary guards.
- Settings dialog width fix (4.3.1).

## [4.2.0] - 2026-07

### Changed
- Split monolithic app into modules (`core`, `theme`, `views`, `ai`, …)
  without intentional behavior change.

## [4.0.0] / [4.1.0] - 2026

### Added
- Offscreen test suite direction; feature work tracked in git history and
  GitHub releases. Prefer release notes on GitHub for pre-4.2 detail until
  this file is backfilled.
