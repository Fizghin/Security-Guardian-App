#!/usr/bin/env bash
# One-time setup in a codespace or dev container: Ollama, Guardian and settings that suit a
# machine without a webcam or speakers.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v ollama >/dev/null; then
  echo "Installing Ollama"
  curl -fsSL https://ollama.com/install.sh | sh
fi
# install.sh downloads the model, which needs the server running
if ! curl -s -o /dev/null http://localhost:11434/api/tags; then
  setsid nohup ollama serve >/tmp/ollama.log 2>&1 </dev/null &
  for _ in $(seq 1 30); do curl -s -o /dev/null http://localhost:11434/api/tags && break; sleep 1; done
fi

# No GPU here: the CPU build of PyTorch is a much smaller download
PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu ./install.sh

set_env() {
  if grep -q "^$1=" backend/.env; then sed -i "s|^$1=.*|$1=$2|" backend/.env; else echo "$1=$2" >>backend/.env; fi
}
# The dashboard gets a link anyone could open once the port is public, so it needs a password
grep -qE '^DASHBOARD_PASSWORD=.+' backend/.env ||
  set_env DASHBOARD_PASSWORD "$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"
# There is no webcam: the first camera is a browser camera (a phone, or the laptop you are on)
set_env VIDEO_SOURCE phone
if [ -n "${CODESPACES:-}" ]; then
  # Phones connect through the codespace's https address (run.sh sets PUBLIC_URL), not a LAN port
  set_env PHONE_PORT 0
fi
