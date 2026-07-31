#!/usr/bin/env bash
# Daily Scheduler — Linux/macOS launcher (mirror of run.bat)
set -e
cd "$(dirname "$0")"
echo "============================================================"
echo "  Daily Scheduler  —  Native Desktop App (Python + Qt6)"
echo "============================================================"
echo

# Project pin: Python 3.14 (see .python-version / spec.md).
PYTHON=""
for cand in python3.14 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        PYTHON=$(command -v "$cand")
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python not found. Install Python 3.14."; exit 1
fi
VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
if [ "$VER" != "3.14" ]; then
    echo "WARNING: preferred runtime is Python 3.14 (found $VER via $PYTHON)."
    echo "         Create .venv with 3.14 when available: python3.14 -m venv .venv"
fi

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
