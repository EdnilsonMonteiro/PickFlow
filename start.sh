#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi
echo "PickFlow em http://127.0.0.1:7860"
exec .venv/bin/python -m pickflow
