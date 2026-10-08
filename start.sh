#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

[ -x venv/bin/python ] || { echo "Guardian is not installed yet. Run ./install.sh first."; exit 1; }
[ -f frontend/dist/index.html ] || { echo "The dashboard has not been built. Run ./install.sh again."; exit 1; }

exec venv/bin/python backend/run_server.py "$@"
