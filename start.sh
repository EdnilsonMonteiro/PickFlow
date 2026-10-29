#!/usr/bin/env bash
# Starts PickFlow (Linux and macOS). The first time it creates the Python environment and installs the dependencies.
# If the file has no execute permission: bash start.sh
set -e
cd "$(dirname "$0")"
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.10+ not found. Install it with your package manager (e.g. sudo apt install python3 python3-venv)."
  exit 1
fi
if [ ! -f .venv/.ready ]; then
  echo "Creating the Python environment (first time only)..."
  [ -x .venv/bin/python ] || "$PY" -m venv .venv || {
    echo "Could not create the environment. On Debian/Ubuntu: sudo apt install python3-venv"
    exit 1
  }
  .venv/bin/python -m pip install -q -r requirements.txt
  touch .venv/.ready
fi
URL="http://127.0.0.1:7860"
echo "PickFlow at $URL"
# open the browser when possible (xdg-open on Linux, open on macOS); if neither exists, just skip
(
  sleep 2
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  elif command -v open >/dev/null 2>&1; then open "$URL"
  fi
) >/dev/null 2>&1 &
exec .venv/bin/python -m pickflow
