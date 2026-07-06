#!/usr/bin/env bash
# Daily Scheduler — Linux/macOS launcher (mirror of run.bat)
set -e
cd "$(dirname "$0")"
echo "============================================================"
echo "  Daily Scheduler  —  Native Desktop App (Python + Qt6)"
echo "============================================================"
echo

PYTHON=$(command -v python3 || command -v python) || {
    echo "ERROR: Python not found. Install it with your package manager."; exit 1; }

echo "Installing / verifying dependencies…"
"$PYTHON" -m pip install --user -r requirements.txt --quiet

echo
echo "Starting Daily Scheduler…"
echo
exec "$PYTHON" app.py
