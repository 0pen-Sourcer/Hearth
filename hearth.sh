#!/usr/bin/env bash
# Mac / Linux launcher for Hearth. Mirrors hearth.bat.
# Run from the repo root:  ./hearth.sh
set -e
cd "$(dirname "$0")"

# Prefer the project venv if one exists; fall back to system python3.
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python" ]; then
    PY=".venv/Scripts/python"
else
    PY="$(command -v python3 || command -v python)"
fi

if [ -z "$PY" ]; then
    echo "error: python3 not found on PATH" >&2
    exit 1
fi

exec "$PY" -X utf8 hearth_cli.py "$@"
