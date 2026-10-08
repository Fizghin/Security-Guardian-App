import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import api as api_module
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def jpeg(w=320, h=240) -> bytes:
    img = np.zeros((h, w, 3), np.uint8)
    cv2.circle(img, (w // 2, h // 2), 40, (0, 200, 0), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def camera(client, cam_id):
    return next(c for c in client.get("/api/status").json()["cameras"] if c["id"] == cam_id)


def test_status_without_camera(client):
    s = client.get("/api/status").json()
    assert s["threat_level"] == 0 and s["panic"] is False
    cam = s["cameras"][0]
    assert cam["id"] == "cam1" and cam["connected"] is False and cam["error"] == "Camera disabled"


def test_arm_disarm_is_logged(client):
    assert client.post("/api/arm", json={"armed": False}).json() == {"armed": False}
    assert client.get("/api/settings").json()["armed"] is False
    assert client.post("/api/arm", json={"armed": True}).json() == {"armed": True}
    types = [e["event_type"] for e in client.get("/api/events?limit=10").json()["items"]]
    assert "DISARMED" in types and "ARMED" in types


def test_phone_camera_end_to_end(client, monkeypatch):
    monkeypatch.setattr(api_module, "PHONE_PORT", 8443)
    cam = client.post("/api/cameras", json={"name": "Back door", "source": "phone"}).json()
    assert cam["kind"] == "phone" and cam["audio"] == "device" and cam["token"]
    token, cam_id = cam["token"], cam["id"]

    pairing = client.get(f"/api/cameras/{cam_id}/pairing").json()
    assert all(token in u and u.startswith("https://") for u in pairing["urls"])

    assert client.get("/api/phone/hello", params={"k": "bad"}).status_code == 401
    assert client.get("/api/phone/hello", params={"k": token}).json()["name"] == "Back door"
    assert client.post("/api/phone/frame", params={"k": "bad"}, content=jpeg()).status_code == 401
    assert client.post("/api/phone/frame", params={"k": token}, content=b"not a jpeg").status_code == 400

    reply = client.post("/api/phone/frame", params={"k": token}, content=jpeg(),
                        headers={"X-Battery": "42", "X-Charging": "1"}).json()
    assert reply["commands"] == [] and reply["config"]["fps"] == 8
    live = camera(client, cam_id)
    assert live["connected"] and (live["width"], live["height"]) == (320, 240)
    assert live["phone"]["battery"] == 42 and live["phone"]["charging"] is True

    # The guard's words are delivered to the phone with the next frame.
    assert client.post("/api/speak", json={"text": "Hello there", "camera_id": cam_id}).status_code == 200
    reply = client.post("/api/phone/frame", params={"k": token}, content=jpeg()).json()
    assert reply["commands"][0]["type"] == "speak" and reply["commands"][0]["text"] == "Hello there"

    deadline = time.time() + 30  # the first frame waits for the person detector to load
    while time.time() < deadline:
        if client.get(f"/api/cameras/{cam_id}/snapshot.jpg").status_code == 200:
            break
        client.post("/api/phone/frame", params={"k": token}, content=jpeg())
        time.sleep(0.05)
    assert client.get(f"/api/cameras/{cam_id}/snapshot.jpg").headers["content-type"] == "image/jpeg"

    assert client.post(f"/api/cameras/{cam_id}/test-intrusion", json={"seconds": 10}).status_code == 200

    client.post("/api/panic")
    s = client.get("/api/status").json()
    assert s["panic"] and s["threat_level"] == 4 and "Back door" in s["alarm_cameras"]
    commands = client.post("/api/phone/heartbeat", params={"k": token}, json={"state": "streaming"}).json()["commands"]
    assert {"type": "siren", "on": True, "seconds": 60} in commands, "the phone sounds the siren"
    assert client.post("/api/alarm/reset").json()["was_active"] is True
    assert client.get("/api/status").json()["panic"] is False

    events = client.get("/api/events", params={"camera": "Back door"}).json()["items"]
    assert events and all(e["camera"] == "Back door" for e in events)

    new = client.post(f"/api/cameras/{cam_id}/reset-link").json()
    assert new["token"] != token
    assert client.post("/api/phone/frame", params={"k": token}, content=jpeg()).status_code == 401

    assert client.patch(f"/api/cameras/{cam_id}", json={"name": "Porch"}).json()["name"] == "Porch"
    assert client.delete(f"/api/cameras/{cam_id}").status_code == 200
    assert client.get(f"/api/cameras/{cam_id}/snapshot.jpg").status_code == 404


def test_phone_cameras_refused_when_disabled(client):
    r = client.post("/api/cameras", json={"name": "Phone", "source": "phone"})
    assert r.status_code == 409


def test_camera_source_test_reports_errors(client):
    r = client.post("/api/cameras/test", json={"source": "/no/such/video.mp4"}).json()
    assert r["ok"] is False and "not found" in r["error"]


def test_test_intrusion_needs_camera(client):
    r = client.post("/api/cameras/cam1/test-intrusion", json={"seconds": 10})
    assert r.status_code == 409
    assert client.post("/api/cameras/nope/test-intrusion", json={"seconds": 10}).status_code == 404


def test_settings_patch_and_validation(client):
    r = client.patch("/api/settings", json={"escalation": {"level2_after": 3, "level3_after": 6, "level4_after": 9}})
    assert r.status_code == 200
    assert r.json()["escalation"]["level3_after"] == 6
    r = client.patch("/api/settings", json={"escalation": {"level4_after": 5}})
    assert r.status_code == 422 and "must increase" in r.json()["detail"]
    r = client.patch("/api/settings", json={"armed": False, "cameras": []})
    assert r.json()["armed"] is True, "arming only through /api/arm"
    assert len(r.json()["cameras"]) >= 1, "cameras only through /api/cameras"


def test_events_filters_summary_and_csv(client):
    r = client.get("/api/events", params={"type": "PANIC"}).json()
    assert r["total"] >= 1 and all(e["event_type"] == "PANIC" for e in r["items"])
    assert r["items"][0]["timestamp"].endswith("Z")
    summary = client.get("/api/events/summary", params={"hours": 24}).json()
    assert summary["bucket"] == "hour" and summary["total"] >= 1 and "Back door" in summary["cameras"]
    csv = client.get("/api/events/export.csv", params={"type": "PANIC"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("id,timestamp_utc,camera,type")
    assert client.get("/api/events", params={"since": "not-a-date"}).status_code == 422


def test_ai_models_reports_unreachable_server(client):
    r = client.get("/api/ai/models").json()
    assert r["models"] == [] and "Cannot reach" in r["error"]


def test_recordings_and_insiders(client):
    recs = client.get("/api/recordings").json()
    assert all(r["camera"] == "Back door" for r in recs["items"]), "only the phone camera recorded (panic)"
    assert client.get("/api/recordings/../../guardian.db").status_code == 404
    assert client.get("/api/insiders").json()["items"] == []
    assert client.delete("/api/insiders/nobody").status_code == 404


def test_system_stats(client):
    s = client.get("/api/system").json()
    assert s["cpu_count"] >= 1 and s["memory_total"] > 0
    assert s["notifications"]["email"] is False and "ready_lines" in s["ai"]
