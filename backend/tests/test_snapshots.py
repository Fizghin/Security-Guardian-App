from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

import api as api_module
from models import database
from models.database import SecurityEvent, SessionLocal, utcnow
from services.event_service import EventService


@pytest.fixture
def events(tmp_path, monkeypatch):
    service = EventService(snapshot_dir=tmp_path / "snapshots")
    service.clear()
    monkeypatch.setattr(api_module, "event_service", service)
    return service


def client() -> TestClient:
    app = FastAPI()
    app.include_router(api_module.router)
    return TestClient(app)


def latest(events, **filters):
    return events.query(limit=1, **filters)["items"][0]


def test_event_keeps_its_picture(events):
    events.log("DETECTION", "Someone at the door", "LOW", camera="Door", snapshot=b"\xff\xd8 picture")
    item = latest(events)
    assert item["snapshot"] is True
    r = client().get(f"/api/events/{item['id']}/snapshot.jpg")
    assert r.status_code == 200 and r.content == b"\xff\xd8 picture" and r.headers["content-type"] == "image/jpeg"


def test_events_without_a_picture(events):
    events.log("ARMED", "System armed")
    item = latest(events)
    assert item["snapshot"] is False
    assert client().get(f"/api/events/{item['id']}/snapshot.jpg").status_code == 404
    assert client().get("/api/events/999999/snapshot.jpg").status_code == 404


def test_stored_names_cannot_point_outside_the_folder(events, tmp_path):
    (tmp_path / "secret.jpg").write_bytes(b"secret")
    events.log("DETECTION", "x", snapshot=b"jpeg")
    db = SessionLocal()
    row = db.query(SecurityEvent).order_by(SecurityEvent.id.desc()).first()
    row.snapshot = "../secret.jpg"
    db.commit()
    event_id = row.id
    db.close()
    assert events.snapshot_path(event_id) is None


def test_clearing_events_deletes_pictures(events):
    events.log("DETECTION", "x", snapshot=b"jpeg")
    assert list(events.snapshot_dir.glob("*.jpg"))
    events.clear()
    assert not list(events.snapshot_dir.glob("*.jpg"))


def test_old_pictures_are_pruned_but_events_stay(events):
    events.log("DETECTION", "old", snapshot=b"old")
    events.log("DETECTION", "new", snapshot=b"new")
    db = SessionLocal()
    old = db.query(SecurityEvent).filter(SecurityEvent.description == "old").one()
    old.timestamp = utcnow() - timedelta(days=40)
    old_file = events.snapshot_dir / old.snapshot
    db.commit()
    db.close()
    assert events.prune_snapshots(0) == 0, "0 keeps everything"
    assert events.prune_snapshots(30) == 1
    assert not old_file.exists()
    items = {e["description"]: e for e in events.query(limit=10)["items"]}
    assert items["old"]["snapshot"] is False and items["new"]["snapshot"] is True


def test_database_from_an_older_version_gets_the_column(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE security_events (id INTEGER PRIMARY KEY, timestamp DATETIME, "
                          "event_type VARCHAR, description VARCHAR, severity VARCHAR)"))
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    columns = {c["name"] for c in inspect(engine).get_columns("security_events")}
    assert {"recording", "camera", "snapshot"} <= columns
