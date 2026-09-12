#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    python3 launcher.py
elif command -v python >/dev/null 2>&1; then
    python launcher.py
else
    echo "Python 3 is required."
    exit 1
fi
