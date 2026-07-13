#!/usr/bin/env bash
# Build a local Linux --onedir package (for personal use on this machine).
# The PUBLIC release binary is built in CI on ubuntu-22.04 for older glibc —
# a Nobara/Fedora build will NOT run on older distros. Prefer:
#   gh workflow run release-linux.yml -f tag=vX.Y.Z
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -q pyinstaller -r requirements.txt
python3 -m PyInstaller --noconfirm --onedir --name DailyScheduler \
  --collect-all PySide6 \
  --add-data "LICENSE:." \
  app.py

chmod +x dist/DailyScheduler/DailyScheduler
echo "Built: dist/DailyScheduler/DailyScheduler"
dist/DailyScheduler/DailyScheduler --version || true
