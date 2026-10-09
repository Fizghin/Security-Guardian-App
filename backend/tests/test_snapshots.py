import os
import threading
import time
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

import api as api_module
from models import database
from models.database import SecurityEvent, SessionLocal, utcnow
from services import event_service as event_module
from services.camera_service import CameraManager
from services.event_service import EventService
from services.settings_service import SettingsService


@pytest.fixture
def events(tmp_path, monkeypatch):
    database.init_db()
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


def backdate(description: str, days: int) -> None:
    db = SessionLocal()
    row = db.query(SecurityEvent).filter(SecurityEvent.description == description).one()
    row.timestamp = utcnow() - timedelta(days=days)
    db.commit()
    db.close()


def test_event_keeps_its_picture(events):
    events.log("DETECTION", "Someone at the door", "LOW", camera="Door", snapshot=b"\xff\xd8 picture")
    item = latest(events)
    assert item["snapshot"].endswith("_detection.jpg")
    r = client().get(f"/api/events/{item['id']}/snapshot.jpg", params={"v": item["snapshot"]})
    assert r.status_code == 200 and r.content == b"\xff\xd8 picture" and r.headers["content-type"] == "image/jpeg"


def test_events_without_a_picture(events):
    events.log("ARMED", "System armed")
    item = latest(events)
    assert item["snapshot"] is None
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
    backdate("old", days=40)
    old_file = events.snapshot_dir / latest(events, search="old")["snapshot"]
    assert events.prune_snapshots(0) == 0, "0 keeps everything"
    assert events.prune_snapshots(30) == 1
    assert not old_file.exists()
    items = {e["description"]: e for e in events.query(limit=10)["items"]}
    assert items["old"]["snapshot"] is None and items["new"]["snapshot"]


def test_picture_url_changes_with_the_file(events):
    # SQLite gives out the same ids again after the log is cleared. Browsers cache pictures,
    # so a URL made from the id alone showed the deleted picture for the new event.
    events.log("DETECTION", "before clearing", snapshot=b"old picture")
    old = latest(events, search="before clearing")
    events.clear()
    events.log("PANIC", "after clearing", snapshot=b"new picture")
    new = latest(events, search="after clearing")
    assert new["id"] == old["id"] and new["snapshot"] != old["snapshot"]
    url = f"/api/events/{new['id']}/snapshot.jpg"
    r = client().get(url, params={"v": new["snapshot"]})
    assert r.status_code == 200 and r.content == b"new picture"
    assert client().get(url, params={"v": old["snapshot"]}).status_code == 404


def test_picture_is_deleted_when_its_event_cannot_be_stored(events, monkeypatch):
    class LockedDatabase:
        def add(self, row):
            pass

        def commit(self):
            raise OperationalError("INSERT", {}, Exception("database is locked"))

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(event_module, "SessionLocal", LockedDatabase)
    events.log("PANIC", "Alarm raised", snapshot=b"jpeg")
    assert not list(events.snapshot_dir.glob("*.jpg"))


def test_event_logged_while_clearing_keeps_its_picture(events, monkeypatch):
    events.log("DETECTION", "old", snapshot=b"old")
    camera = threading.Thread(target=events.log, args=("DETECTION", "new"), kwargs={"snapshot": b"new"})
    real = event_module.SessionLocal

    def session():
        db = real()
        if camera.ident is None:  # the session clear() uses
            commit = db.commit

            def commit_then_log():
                commit()
                camera.start()  # a camera logs an event right after the rows are deleted
                camera.join(0.5)
            db.commit = commit_then_log
        return db

    monkeypatch.setattr(event_module, "SessionLocal", session)
    events.clear()
    camera.join()
    item = latest(events, search="new")
    assert events.snapshot_path(item["id"]) is not None


def test_pictures_no_event_refers_to_are_pruned(events):
    events.log("DETECTION", "kept", snapshot=b"jpeg")
    kept = events.snapshot_dir / latest(events, search="kept")["snapshot"]
    stray = events.snapshot_dir / "20261001-120000-000000_panic.jpg"
    stray.write_bytes(b"left by a failed database write")
    being_stored = events.snapshot_dir / "20261009-120000-000000_alert.jpg"
    being_stored.write_bytes(b"its event is being stored right now")
    two_hours_ago = time.time() - 7200
    for path in (kept, stray):
        os.utime(path, (two_hours_ago, two_hours_ago))
    assert events.prune_snapshots(0) == 1, "also when pictures are kept forever"
    assert kept.exists() and not stray.exists() and being_stored.exists()


def test_retention_applies_every_hour_without_any_clip(events, tmp_path):
    # Recognised people and incidents below the recording level get pictures but never a clip.
    manager = CameraManager(settings=SettingsService(tmp_path / "settings.json"), events=events)
    events.log("INSIDER", "Recognised Sam", snapshot=b"jpeg")
    backdate("Recognised Sam", days=40)
    start = manager._pruned_at
    manager.prune_old_media(start + 600)
    assert latest(events, search="Sam")["snapshot"], "not due yet"
    manager.prune_old_media(start + 3600)
    assert latest(events, search="Sam")["snapshot"] is None
    assert not list(events.snapshot_dir.glob("*.jpg"))


def test_database_from_an_older_version_gets_the_column(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE security_events (id INTEGER PRIMARY KEY, timestamp DATETIME, "
                          "event_type VARCHAR, description VARCHAR, severity VARCHAR)"))
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    columns = {c["name"] for c in inspect(engine).get_columns("security_events")}
    assert {"recording", "camera", "snapshot"} <= columns
