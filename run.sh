#!/usr/bin/env bash
# DSA Review launcher (macOS / Linux). Usage: ./run.sh [--port 9000] [--no-browser]
set -euo pipefail
cd "$(dirname "$0")"
# (Re)do the setup if it never finished, e.g. the install was interrupted.
if ! .venv/bin/python -c "import fsrs" >/dev/null 2>&1; then
  echo "Setting up DSA Review (first run)..."
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    found=$( (command -v python3 >/dev/null && python3 --version 2>&1) || echo "no python3 on PATH")
    echo "DSA Review needs Python 3.10 or newer (found: $found)." >&2
    exit 1
  fi
  python3 -m venv --clear .venv
  echo "Installing fsrs (needs internet, takes about a minute)..."
  if ! .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt; then
    rm -rf .venv
    echo "Failed to install requirements. Check your internet connection and try again." >&2
    exit 1
  fi
fi
exec .venv/bin/python app/server.py "$@"
