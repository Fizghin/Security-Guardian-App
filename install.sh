#!/usr/bin/env bash
# Guardian installer for Linux and macOS.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "Python 3.10-3.13 is required"; exit 1; }
command -v npm >/dev/null || { echo "Node.js (LTS) is required: https://nodejs.org"; exit 1; }

echo "[1/4] Python environment"
[ -d venv ] || "$PY" -m venv venv
./venv/bin/python -m pip install --upgrade pip >/dev/null

echo "[2/4] Python packages (first run downloads ~1 GB, mostly PyTorch)"
./venv/bin/pip install -r backend/requirements.txt
./venv/bin/pip install -r backend/requirements-optional.txt || echo "      Sound recognition and the Guard Bot are not available on this computer (optional)"
[ -f backend/.env ] || { cp backend/.env.template backend/.env; echo "      Created backend/.env"; }

echo "[3/4] Dashboard"
(cd frontend && npm ci && npm run build)

echo "[4/4] Local language model"
if command -v ollama >/dev/null; then
  if ollama list 2>/dev/null | grep -q ':'; then
    echo "      Ollama already has a model installed."
  else
    echo "      Downloading llama3.2:3b (about 2 GB)..."
    ollama pull llama3.2:3b
  fi
else
  echo "      Ollama is not installed. Install it from https://ollama.com, then: ollama pull llama3.2:3b"
  echo "      Guardian works without it, but speaks pre-written warnings instead."
fi

if [ "$(uname)" = "Linux" ] && ! command -v espeak-ng >/dev/null && ! command -v espeak >/dev/null; then
  echo
  echo "Note: no speech engine found. For spoken warnings install espeak-ng (e.g. sudo apt install espeak-ng)."
fi

echo
echo "Done. Start Guardian with ./start.sh"
