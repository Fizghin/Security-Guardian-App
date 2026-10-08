#!/usr/bin/env bash
# Starts Guardian in the background if it isn't running, then shows how to open it.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=/tmp/guardian.log

if [ -n "${CODESPACE_NAME:-}" ]; then
  export PUBLIC_URL="https://${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
fi

if ! curl -s -o /dev/null http://localhost:8000/api/health; then
  echo "Starting Guardian (log: $LOG)…"
  setsid nohup ./start.sh --no-browser >"$LOG" 2>&1 </dev/null &
  for _ in $(seq 1 180); do curl -s -o /dev/null http://localhost:8000/api/health && break; sleep 1; done
  if ! curl -s -o /dev/null http://localhost:8000/api/health; then
    echo "Guardian did not start. Last lines of $LOG:"
    tail -20 "$LOG"
    exit 1
  fi
fi

password=$(sed -n 's/^DASHBOARD_PASSWORD=//p' backend/.env)
camera=$(curl -s -u "guardian:$password" http://localhost:8000/api/cameras/cam1/pairing |
  python3 -c 'import json, sys; print((json.load(sys.stdin).get("urls") or [""])[0])' 2>/dev/null || true)

echo
echo "  Guardian:  ${PUBLIC_URL:-http://localhost:8000}   (Ports tab → Guardian)"
echo "  Password:  ${password:-none}"
if [ -n "$camera" ]; then
  echo "  Camera:    $camera"
  echo "             Open it on a phone, or in another tab on this laptop, to use that camera."
  echo "             For a phone, first make port 8000 Public (Ports tab → right-click → Port Visibility)."
fi
echo "  Log:       $LOG"
