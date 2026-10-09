"""
Paths and environment for the Guardian backend.

Every path is anchored to this file's directory, so the server behaves the same
whether it is started from the repo root, from backend/, or from a script.
"""
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent

# backend/.env wins over a repo-level .env; real environment variables win over both.
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")
# OpenCV prints internal warnings (camera probing, model backends) that mean nothing to users
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

DATA_DIR = Path(os.getenv("GUARDIAN_DATA_DIR", BACKEND_DIR / "storage")).resolve()
RECORDINGS_DIR = DATA_DIR / "recordings"
FACES_DIR = DATA_DIR / "faces"
MODELS_DIR = DATA_DIR / "models"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"  # pictures attached to events
VISITORS_DIR = DATA_DIR / "visitors"  # face crops of remembered strangers, one folder per visitor
SETTINGS_FILE = DATA_DIR / "settings.json"
SCHEDULE_STATE_FILE = DATA_DIR / "schedule_state.json"  # the schedule event last acted on
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'guardian.db').as_posix()}"

YOLO_MODEL = os.getenv("YOLO_MODEL", str(BACKEND_DIR / "yolov8n.pt"))
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
# HTTPS port that phones on the local network use as cameras (0 turns phone cameras off).
# It only serves the phone camera page; the dashboard stays on HOST:PORT.
PHONE_PORT = int(os.getenv("PHONE_PORT", "8443"))

for _d in (DATA_DIR, RECORDINGS_DIR, FACES_DIR, MODELS_DIR, SNAPSHOTS_DIR, VISITORS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


# Password for the dashboard. Set it whenever other devices can reach the dashboard
# (HOST=0.0.0.0, a reverse proxy or a tunnel). Phones keep using their pairing links.
DASHBOARD_PASSWORD = env_str("DASHBOARD_PASSWORD")
# The https address Guardian is reachable at through a reverse proxy or tunnel, e.g.
# https://guardian.example.com. Phone pairing links then use it first.
PUBLIC_URL = env_str("PUBLIC_URL").rstrip("/")
# Phones reach Guardian on the LAN port, through PUBLIC_URL, or both.
PHONES_ENABLED = PHONE_PORT > 0 or bool(PUBLIC_URL)


def migrate_legacy_data() -> None:
    """
    Earlier versions wrote sql_app.db and faces_db/ relative to whatever the
    working directory happened to be. Pull those into storage/ once so existing
    event history and insider photos are not lost.
    """
    new_db = DATA_DIR / "guardian.db"
    if not os.getenv("DATABASE_URL") and not new_db.exists():
        for old in (BACKEND_DIR / "sql_app.db", ROOT_DIR / "sql_app.db"):
            if old.exists():
                shutil.copy2(old, new_db)
                print(f"[config] Migrated event database from {old}")
                break

    for old_faces in (BACKEND_DIR / "faces_db", BACKEND_DIR / "backend" / "faces_db", ROOT_DIR / "backend" / "faces_db"):
        if not old_faces.is_dir():
            continue
        for person_dir in old_faces.iterdir():
            if person_dir.is_dir() and not (FACES_DIR / person_dir.name).exists():
                shutil.copytree(person_dir, FACES_DIR / person_dir.name)
                print(f"[config] Migrated insider photos for {person_dir.name}")
