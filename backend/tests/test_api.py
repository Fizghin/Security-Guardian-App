import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_status_without_camera(client):
    s = client.get("/api/status").json()
    assert s["threat_level"] == 0
    assert s["camera"]["connected"] is False
    assert s["camera"]["error"] == "Camera disabled"
    assert s["pipeline"]["running"] is True


def test_arm_disarm_is_logged(client):
    assert client.post("/api/arm", json={"armed": False}).json() == {"armed": False}
    assert client.get("/api/settings").json()["armed"] is False
    assert client.post("/api/arm", json={"armed": True}).json() == {"armed": True}
    types = [e["event_type"] for e in client.get("/api/events?limit=10").json()["items"]]
    assert "DISARMED" in types and "ARMED" in types


def test_panic_and_reset(client):
    client.post("/api/panic")
    s = client.get("/api/status").json()
    assert s["threat_level"] == 4 and s["manual_alarm"] is True
    assert client.post("/api/alarm/reset").json()["was_active"] is True
    assert client.get("/api/status").json()["threat_level"] == 0


def test_test_intrusion_needs_camera(client):
    r = client.post("/api/test-intrusion", json={"seconds": 10})
    assert r.status_code == 409


def test_settings_patch_and_validation(client):
    r = client.patch("/api/settings", json={"escalation": {"level2_after": 3, "level3_after": 6, "level4_after": 9}})
    assert r.status_code == 200
    assert r.json()["escalation"]["level3_after"] == 6
    r = client.patch("/api/settings", json={"escalation": {"level4_after": 5}})
    assert r.status_code == 422
    assert "must increase" in r.json()["detail"]
    r = client.patch("/api/settings", json={"armed": False})
    assert r.json()["armed"] is True, "arming only through /api/arm"


def test_events_filters_summary_and_csv(client):
    r = client.get("/api/events", params={"type": "PANIC"}).json()
    assert r["total"] >= 1 and all(e["event_type"] == "PANIC" for e in r["items"])
    assert r["items"][0]["timestamp"].endswith("Z")
    summary = client.get("/api/events/summary", params={"hours": 24}).json()
    assert summary["bucket"] == "hour" and summary["total"] >= 1
    assert summary["by_severity"]["CRITICAL"] >= 1
    csv = client.get("/api/events/export.csv", params={"type": "PANIC"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("id,timestamp_utc,type")
    assert client.get("/api/events", params={"since": "not-a-date"}).status_code == 422


def test_ai_models_reports_unreachable_server(client):
    r = client.get("/api/ai/models").json()
    assert r["models"] == [] and "Cannot reach" in r["error"]


def test_recordings_and_insiders_empty(client):
    assert client.get("/api/recordings").json()["items"] == []
    assert client.get("/api/recordings/../../guardian.db").status_code == 404
    assert client.get("/api/insiders").json()["items"] == []
    assert client.delete("/api/insiders/nobody").status_code == 404


def test_snapshot_unavailable_without_camera(client):
    assert client.get("/api/snapshot.jpg").status_code == 503


def test_system_stats(client):
    s = client.get("/api/system").json()
    assert s["cpu_count"] >= 1 and s["memory_total"] > 0
    assert "notifications" in s and s["notifications"]["email"] is False
