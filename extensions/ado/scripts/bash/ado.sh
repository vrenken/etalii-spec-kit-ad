#!/usr/bin/env bash

# Thin launcher for the ado extension's Python helper.
#
# All behaviour lives in ../python/ado.py so the bash, PowerShell and Python
# entry points cannot drift apart. Usage: ./ado.sh <command> [options]

set -e

SCRIPT_DIR="$(CDPATH="" cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/../python/ado.py"

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$HELPER" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$HELPER" "$@"
fi

echo '{"error": "Python 3 is required for the ado extension but was not found on PATH."}' >&2
exit 1
