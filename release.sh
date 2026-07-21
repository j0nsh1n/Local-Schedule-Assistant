#!/usr/bin/env bash
# One-command release cut for Daily Scheduler.
#
# After a version PR is merged to main (with __version__ already bumped in
# core.py), this tags that commit and publishes a GitHub Release. CI then
# builds and attaches:
#   • DailyScheduler-win64.zip (+ .sha256)
#   • DailyScheduler-linux-x86_64.zip (+ .sha256)
#   • DailyScheduler-x86_64.AppImage (+ .sha256)
#
# Usage:
#   ./release.sh                     # use __version__ from core.py
#   ./release.sh 4.1.0               # require this version matches core.py
#   ./release.sh --notes notes.md    # release body from file
#   ./release.sh --draft             # create as draft (CI still needs publish)
#   ./release.sh --skip-checks       # skip syntax/ruff/tests
#   ./release.sh --yes               # no interactive confirm
#   ./release.sh --dry-run           # print plan, make no changes
#
# Does NOT edit core.py or open a PR — bump the version in a normal PR first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

NOTES_FILE=""
DRAFT=0
SKIP_CHECKS=0
YES=0
DRY_RUN=0
WANT_VERSION=""

usage() {
  # Print the leading comment block (usage docs), strip the "# " prefix.
  sed -n '2,/^set -euo pipefail$/p' "$0" | sed '$d' | sed 's/^# \?//'
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --notes) NOTES_FILE="${2:-}"; shift 2 ;;
    --draft) DRAFT=1; shift ;;
    --skip-checks) SKIP_CHECKS=1; shift ;;
    --yes|-y) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    -*)
      echo "unknown flag: $1" >&2
      usage
      ;;
    *)
      if [[ -n "$WANT_VERSION" ]]; then
        echo "unexpected argument: $1" >&2
        usage
      fi
      WANT_VERSION="$1"
      shift
      ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }
info() { echo "→ $*"; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required"
}

need git
need gh
need python3

# ── Version from core.py (moved out of app.py in the v4.2.0 module split) ─
APP_VERSION="$(python3 - <<'PY'
import re, pathlib
t = pathlib.Path("core.py").read_text(encoding="utf-8")
m = re.search(r'^__version__\s*=\s*"([^"]+)"', t, re.M)
if not m:
    raise SystemExit("could not parse __version__ from core.py")
print(m.group(1))
PY
)"
[[ -n "$APP_VERSION" ]] || die "empty __version__"

if [[ -n "$WANT_VERSION" ]]; then
  WANT_VERSION="${WANT_VERSION#v}"
  [[ "$WANT_VERSION" == "$APP_VERSION" ]] || \
    die "core.py is $APP_VERSION but you asked for $WANT_VERSION — bump/merge first"
fi

TAG="v${APP_VERSION}"
TITLE="Daily Scheduler ${TAG}"

# ── Repo hygiene ─────────────────────────────────────────────────────────
[[ -z "$(git status --porcelain)" ]] || die "working tree is dirty — commit or stash first"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$BRANCH" != "main" ]]; then
  die "on branch '$BRANCH' — checkout main (release tags should point at main)"
fi

info "Fetching origin…"
git fetch origin --tags --quiet

# Prefer releasing the tip of origin/main.
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main 2>/dev/null || true)"
[[ -n "$REMOTE" ]] || die "origin/main not found — is the remote set up?"
if [[ "$LOCAL" != "$REMOTE" ]]; then
  die "HEAD ($LOCAL) != origin/main ($REMOTE) — git pull (or push) first"
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  die "tag $TAG already exists locally"
fi
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q .; then
  die "tag $TAG already exists on origin"
fi
if gh release view "$TAG" >/dev/null 2>&1; then
  die "GitHub release $TAG already exists"
fi

# ── Checks ───────────────────────────────────────────────────────────────
if [[ "$SKIP_CHECKS" -eq 0 ]]; then
  info "Syntax check…"
  python3 -m py_compile *.py

  if command -v ruff >/dev/null 2>&1; then
    info "ruff (real-bug rules)…"
    ruff check *.py --select E9,F63,F7,F82
  else
    info "ruff not installed — skipping lint (CI still runs it)"
  fi

  if [[ -d tests ]]; then
    info "Offscreen tests…"
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
    failed=0
    for t in tests/test_*.py; do
      set +e
      out="$(python3 "$t" 2>&1)"
      ec=$?
      set -e
      if echo "$out" | grep -q '\[FAIL\]'; then
        echo "$out"
        echo "→ FAIL assertions in $t" >&2
        failed=1
      elif echo "$out" | grep -qE 'RESULT: PASS|[0-9]+/[0-9]+ passed'; then
        :
      else
        echo "$out"
        echo "→ FAIL $t (exit $ec, no success marker)" >&2
        failed=1
      fi
    done
    [[ "$failed" -eq 0 ]] || die "tests failed — fix before releasing"
    info "tests ok"
  fi
else
  info "Skipping checks (--skip-checks)"
fi

# ── Notes ────────────────────────────────────────────────────────────────
NOTES_TMP=""
cleanup_notes() {
  [[ -n "${NOTES_TMP:-}" && -f "${NOTES_TMP:-}" ]] && rm -f "$NOTES_TMP"
}
trap cleanup_notes EXIT

if [[ -n "$NOTES_FILE" ]]; then
  [[ -f "$NOTES_FILE" ]] || die "notes file not found: $NOTES_FILE"
else
  PREV="$(git describe --tags --abbrev=0 2>/dev/null || true)"
  NOTES_TMP="$(mktemp "${TMPDIR:-/tmp}/ds-relnotes.XXXXXX")"
  {
    echo "## What's new in ${TAG}"
    echo
    if [[ -n "$PREV" ]]; then
      echo "Changes since ${PREV}:"
      echo
      git log --pretty=format:'- %s' "${PREV}..HEAD"
      echo
    else
      echo "(No previous tag found — add release notes.)"
    fi
    echo
    echo "## Downloads"
    echo
    echo "| Asset | Platform |"
    echo "|---|---|"
    echo "| \`DailyScheduler-win64.zip\` | Windows 10/11 (onedir) |"
    echo "| \`DailyScheduler-linux-x86_64.zip\` | Linux x86_64 (onedir, glibc ≥ 2.35) |"
    echo "| \`DailyScheduler-x86_64.AppImage\` | Linux x86_64 (single file) |"
    echo
    echo "SHA-256 checksums ship alongside each asset (\`*.sha256\`)."
    echo
    echo "CI attaches the binaries a few minutes after this release is published."
  } > "$NOTES_TMP"
  NOTES_FILE="$NOTES_TMP"

  if [[ "$YES" -eq 0 && "$DRY_RUN" -eq 0 && -t 0 ]]; then
    ${EDITOR:-${VISUAL:-nano}} "$NOTES_FILE" || true
  fi
fi

# ── Confirm ──────────────────────────────────────────────────────────────
info "Plan:"
echo "  version : $APP_VERSION"
echo "  tag     : $TAG"
echo "  commit  : $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"
echo "  draft   : $([[ $DRAFT -eq 1 ]] && echo yes || echo no)"
echo "  notes   : $NOTES_FILE"
echo

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "Dry run — no tag, no release."
  exit 0
fi

if [[ "$YES" -eq 0 ]]; then
  read -r -p "Create and publish $TAG? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || die "aborted"
fi

# ── Tag + release ────────────────────────────────────────────────────────
info "Creating annotated tag $TAG…"
git tag -a "$TAG" -m "$TITLE"

info "Pushing tag to origin…"
git push origin "$TAG"

GH_ARGS=(release create "$TAG" --title "$TITLE" --notes-file "$NOTES_FILE")
if [[ "$DRAFT" -eq 1 ]]; then
  GH_ARGS+=(--draft)
  info "Creating DRAFT release (publish it in the UI or with: gh release edit $TAG --draft=false)"
else
  GH_ARGS+=(--latest)
  info "Publishing release (triggers Windows + Linux CI asset builds)…"
fi
gh "${GH_ARGS[@]}"

echo
info "Done. Release page:"
gh release view "$TAG" --json url -q .url 2>/dev/null || echo "  https://github.com/j0nsh1n/Local-Schedule-Assistant/releases/tag/$TAG"
echo
info "Watch asset builds:"
echo "  gh run list --workflow=release-linux.yml --limit 3"
echo "  gh run list --workflow=release-windows.yml --limit 3"
echo "  gh release view $TAG"
