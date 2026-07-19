#!/usr/bin/env bash
# Wrap a PyInstaller --onedir tree into a type-2 AppImage.
#
# Usage:
#   packaging/make-appimage.sh <onedir-dir> [output.AppImage]
#
# The onedir dir must contain the DailyScheduler binary (and _internal/).
# Public release AppImages are built in CI on ubuntu-22.04 (older glibc).
# A Nobara/Fedora-built AppImage will NOT run on older distros — same rule
# as the onedir zip.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ONEDIR="${1:-}"
OUT="${2:-}"

if [[ -z "$ONEDIR" || ! -d "$ONEDIR" ]]; then
  echo "usage: $0 <onedir-dir> [output.AppImage]" >&2
  exit 2
fi
ONEDIR="$(cd "$ONEDIR" && pwd)"

BIN=""
for candidate in DailyScheduler daily-scheduler; do
  if [[ -x "$ONEDIR/$candidate" ]]; then
    BIN="$candidate"
    break
  fi
done
if [[ -z "$BIN" ]]; then
  echo "error: no executable DailyScheduler in $ONEDIR" >&2
  ls -la "$ONEDIR" >&2 || true
  exit 1
fi

if [[ -z "$OUT" ]]; then
  OUT="$(dirname "$ONEDIR")/DailyScheduler-x86_64.AppImage"
fi
OUT="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ds-appimage.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

APPDIR="$WORK/AppDir"
mkdir -p "$APPDIR/usr/bin" \
         "$APPDIR/usr/lib/DailyScheduler" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy the whole onedir next to a stable path; AppRun launches from there so
# PyInstaller's relative _internal/ layout keeps working.
cp -a "$ONEDIR"/. "$APPDIR/usr/lib/DailyScheduler/"
chmod +x "$APPDIR/usr/lib/DailyScheduler/$BIN"

# Thin launcher on PATH for the desktop Exec= line.
cat > "$APPDIR/usr/bin/DailyScheduler" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/../lib/DailyScheduler/DailyScheduler" "$@"
EOF
chmod +x "$APPDIR/usr/bin/DailyScheduler"

# AppRun: entry point when the AppImage is executed.
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:${PATH:-}"
# Prefer bundled Qt/platform plugins over any host copies.
export QT_PLUGIN_PATH="${HERE}/usr/lib/DailyScheduler/_internal/PySide6/Qt/plugins${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
exec "$HERE/usr/lib/DailyScheduler/DailyScheduler" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# Desktop entry + icon (appimagetool wants these at AppDir root too).
cp "$ROOT/packaging/daily-scheduler.desktop" "$APPDIR/daily-scheduler.desktop"
cp "$ROOT/packaging/daily-scheduler.desktop" "$APPDIR/usr/share/applications/daily-scheduler.desktop"

ICON_PNG="$APPDIR/daily-scheduler.png"
if command -v python3 >/dev/null 2>&1; then
  QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
    python3 "$ROOT/packaging/render_icon.py" "$ICON_PNG" 256
else
  echo "error: python3 required to render the AppImage icon" >&2
  exit 1
fi
cp "$ICON_PNG" "$APPDIR/usr/share/icons/hicolor/256x256/apps/daily-scheduler.png"
# Symlink without extension — some appimagetool versions look for this.
ln -sf daily-scheduler.png "$APPDIR/.DirIcon"

# Fetch appimagetool (pinned) if not already on PATH.
TOOL=""
if command -v appimagetool >/dev/null 2>&1; then
  TOOL="$(command -v appimagetool)"
else
  TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.0/appimagetool-x86_64.AppImage"
  TOOL="$WORK/appimagetool"
  echo "Downloading appimagetool 1.9.0…"
  curl -fsSL "$TOOL_URL" -o "$TOOL"
  chmod +x "$TOOL"
fi

# CI runners often lack FUSE; extract-and-run avoids mounting the tool itself.
export APPIMAGE_EXTRACT_AND_RUN=1
export ARCH=x86_64

# VERSION is embedded in some AppImage metadata; prefer app.py if readable.
if [[ -z "${VERSION:-}" && -f "$ROOT/app.py" ]]; then
  VERSION="$(python3 -c "import re,pathlib; t=pathlib.Path('$ROOT/app.py').read_text(); m=re.search(r'__version__\s*=\s*\"([^\"]+)\"', t); print(m.group(1) if m else '')")"
  export VERSION
fi

echo "Building AppImage → $OUT"
# appimagetool writes next to AppDir unless given an output path.
if [[ "$TOOL" == *.AppImage ]] || [[ "$(basename "$TOOL")" == appimagetool* ]]; then
  "$TOOL" "$APPDIR" "$OUT"
else
  appimagetool "$APPDIR" "$OUT"
fi

chmod +x "$OUT"
# Leave the temp AppDir cleanup to the trap; keep only the AppImage.
ls -lh "$OUT"
sha256sum "$OUT" | tee "${OUT}.sha256"
echo "Built: $OUT"
