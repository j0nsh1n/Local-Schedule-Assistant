#!/usr/bin/env bash
# Build a local Linux --onedir package (for personal use on this machine).
# The PUBLIC release binary is built in CI on ubuntu-22.04 for older glibc —
# a Nobara/Fedora build will NOT run on older distros. Prefer:
#   ./release.sh                  # tag + publish; CI attaches zip + AppImage
#   gh workflow run release-linux.yml -f tag=vX.Y.Z
#
# Optional: ./build-linux.sh --appimage  also wraps the onedir (local glibc!).
set -euo pipefail
cd "$(dirname "$0")"

MAKE_APPIMAGE=0
for arg in "$@"; do
  case "$arg" in
    --appimage) MAKE_APPIMAGE=1 ;;
    -h|--help)
      echo "usage: $0 [--appimage]"
      exit 0
      ;;
  esac
done

python3 -m pip install -q pyinstaller -r requirements.txt
# Collect shiboken6 explicitly — required by PySide6's DLL search at import.
# Without it, freezes can fail with: ImportError: .../shiboken6 does not exist
# (bootloader: "Failed to execute script 'app'" from entry module app.py).
python3 -m PyInstaller --noconfirm --onedir --name DailyScheduler \
  --collect-all PySide6 \
  --collect-all shiboken6 \
  --hidden-import shiboken6 \
  --copy-metadata PySide6 \
  --copy-metadata shiboken6 \
  --add-data "LICENSE:." \
  app.py

chmod +x dist/DailyScheduler/DailyScheduler
echo "Built: dist/DailyScheduler/DailyScheduler"
dist/DailyScheduler/DailyScheduler --version || true

if [[ "$MAKE_APPIMAGE" -eq 1 ]]; then
  chmod +x packaging/make-appimage.sh
  packaging/make-appimage.sh dist/DailyScheduler dist/DailyScheduler-x86_64.AppImage
  echo "Local AppImage (this distro's glibc only): dist/DailyScheduler-x86_64.AppImage"
fi
