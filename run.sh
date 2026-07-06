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

# PEP 668: modern distros (Debian 12+, Ubuntu 23.04+) refuse pip installs outside
# a virtual environment — even --user — so keep dependencies in a project-local
# .venv (created on first run, reused after).
if [ ! -x .venv/bin/python ]; then
    echo "Creating virtual environment (.venv)…"
    "$PYTHON" -m venv .venv || {
        echo "ERROR: couldn't create a venv (on Debian/Ubuntu: sudo apt install python3-venv)"
        exit 1; }
fi

echo "Installing / verifying dependencies…"
.venv/bin/python -m pip install -r requirements.txt --quiet

echo
echo "Starting Daily Scheduler…"
echo
exec .venv/bin/python app.py
