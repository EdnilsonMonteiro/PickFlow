#!/usr/bin/env bash
# Starts the Clef server. Everything happens in start_clef.py (creates the environment the first time, asks bf16 or int4 and starts the
# server); this file only finds Python. To skip the question: ./start_clef.sh --quant int4
cd "$(dirname "$0")" || exit 1
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" start_clef.py "$@"
