"""
Test setup: everything writes into a throw-away data directory, the camera is
disabled, and the language model points at a closed port so tests never depend
on local hardware or a running Ollama.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

_data_dir = Path(tempfile.mkdtemp(prefix="guardian-test-"))
os.environ["GUARDIAN_DATA_DIR"] = str(_data_dir)
os.environ["VIDEO_SOURCE"] = "none"
os.environ["PHONE_PORT"] = "0"  # no HTTPS listener during tests
os.environ["AI_PROVIDER"] = "ollama"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
os.environ.pop("DATABASE_URL", None)
for key in ("DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK", "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL"):
    os.environ[key] = ""
# Face models are a 38 MB download; keep the test run offline.
(_data_dir / "settings.json").write_text(json.dumps({"detection": {"face_recognition": False}}))
