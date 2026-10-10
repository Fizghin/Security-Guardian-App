import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from models.domain import Detection
from services.heatmap_service import COLS, HOUR, KEEP_DAYS, ZONE, HeatmapService, heatmap_service

T = 1_760_000_000.0  # a fixed moment, so hour buckets are predictable
W, H = 640, 360


def person(x: float, y: float, track: int | None = 1, **kwargs) -> Detection:
    """Someone standing with their feet at (x, y)."""
    return Detection(confidence=0.9, bbox=[x - 20, y - 120, x + 20, y], track_id=track, **kwargs)


def new_service(tmp_path, now=T) -> HeatmapService:
    return HeatmapService(tmp_path / "heatmaps", clock=lambda: now)


def test_foot_points_are_added_at_most_twice_a_second_per_person(tmp_path):
    svc = new_service(tmp_path)
    assert svc.add("cam1", [person(120, 200)], W, H, T) == 1
    assert svc.add("cam1", [person(120, 200)], W, H, T + 0.2) == 0  # same person, too soon
    assert svc.add("cam1", [person(120, 200), person(500, 300, track=2)], W, H, T + 0.6) == 2
    assert svc.add("cam1", [person(120, 200, track=None)] * 2, W, H, T + 0.7) == 2  # untracked: every detection
    ignored = [person(300, 300, track=3, simulated=True), Detection(**{"class": "car"}, confidence=0.9,
                                                                     bbox=[0, 0, 50, 50], track_id=4)]
    assert svc.add("cam1", ignored, W, H, T + 1) == 0

    grid = svc.grid("cam1", 24, T + 2)
    assert grid.shape == (36, COLS) and grid.sum() == 5
    assert grid[int(200 / H * 36), int(120 / W * COLS)] == 4  # the bottom centre of the box
    assert grid[int(300 / H * 36), int(500 / W * COLS)] == 1


def test_points_at_the_edge_stay_in_the_grid(tmp_path):
    svc = new_service(tmp_path)
    svc.add("cam1", [person(W + 30, H + 5)], W, H, T)
    assert svc.grid("cam1", 1, T)[-1, -1] == 1
    # A portrait phone picture gets more rows than columns allow; they are capped
    svc.add("phone", [person(100, 600, track=None)], 360, 640, T)
    assert svc.grid("phone", 1, T).shape == (COLS, COLS)


def test_hours_and_summary(tmp_path):
    svc = new_service(tmp_path)
    for i in range(6):
        svc.add("cam1", [person(100, 100, track=None)], W, H, T - 3 * HOUR)
    for i in range(2):
        svc.add("cam1", [person(100, 100, track=None)], W, H, T - 30 * HOUR)
    assert svc.grid("cam1", 24, T).sum() == 6
    assert svc.grid("cam1", 168, T).sum() == 8
    summary = svc.summary("cam1", 168, T)
    assert summary["total"] == 8 and summary["empty"] is False and sum(summary["by_hour"]) == 8
    assert [b["points"] for b in summary["busiest_hours"]] == [6, 2]
    assert summary["busiest_hours"][0]["share"] == 0.75
    assert svc.summary("other", 24, T) == {"hours": 24, "total": 0, "empty": True, "by_hour": [0] * 24,
                                           "busiest_hours": []}


def test_saved_and_read_back(tmp_path):
    svc = new_service(tmp_path)
    svc.add("cam1", [person(120, 200)], W, H, T)
    svc.add("cam1", [person(120, 200)], W, H, T + HOUR)
    svc.save("cam1")
    path = tmp_path / "heatmaps" / "cam1.json"
    assert len(json.loads(path.read_text())["hours"]) == 2
    mtime = path.stat().st_mtime_ns
    svc.save("cam1")  # nothing new: not written again
    assert path.stat().st_mtime_ns == mtime

    again = new_service(tmp_path, now=T + HOUR)
    np.testing.assert_array_equal(again.grid("cam1", 24), svc.grid("cam1", 24, T + HOUR))
    again.add("cam1", [person(120, 200, track=None)], W, H, T + HOUR)
    assert again.grid("cam1", 24).sum() == 3


def test_saving_happens_every_few_minutes(tmp_path):
    now = [T]
    svc = HeatmapService(tmp_path / "heatmaps", clock=lambda: now[0])
    svc.add("cam1", [person(120, 200)], W, H)
    svc.tick()
    assert not (tmp_path / "heatmaps" / "cam1.json").exists()
    now[0] += 301
    svc.tick()
    assert (tmp_path / "heatmaps" / "cam1.json").exists()


def test_old_hours_are_pruned(tmp_path):
    svc = new_service(tmp_path)
    old = T - (KEEP_DAYS + 1) * 86400
    svc.add("cam1", [person(120, 200, track=None)], W, H, old)
    svc.add("cam1", [person(120, 200, track=None)], W, H, T)
    svc.add("gone", [person(120, 200, track=None)], W, H, old)  # a camera that was removed since
    svc.save_all()
    assert svc.prune(T) == 2
    assert svc.grid("cam1", 24 * 60, T).sum() == 1
    assert list(json.loads((tmp_path / "heatmaps" / "cam1.json").read_text())["hours"]) == [str(int(T // HOUR) * HOUR)]
    assert not (tmp_path / "heatmaps" / "gone.json").exists()

    # Hours that expired while Guardian was off are dropped when the file is read
    stale = new_service(tmp_path)
    stale.add("cam2", [person(1, 1, track=None)], W, H, old)
    stale.save("cam2")
    fresh = new_service(tmp_path)
    fresh.load("cam2")
    assert fresh.grid("cam2", 24 * 60).sum() == 0
    fresh.save_all()
    assert not (tmp_path / "heatmaps" / "cam2.json").exists()


def test_forget(tmp_path):
    svc = new_service(tmp_path)
    svc.add("cam1", [person(120, 200)], W, H, T)
    svc.save("cam1")
    svc.forget("cam1")
    assert not (tmp_path / "heatmaps" / "cam1.json").exists() and svc.grid("cam1", 24, T).sum() == 0
    with pytest.raises(KeyError):  # camera ids become file names
        svc.add("../evil", [person(1, 1)], W, H, T)


def decode(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def test_render_puts_the_hotspot_where_people_stood(tmp_path):
    svc = new_service(tmp_path)
    for i in range(40):
        svc.add("cam1", [person(160, 300, track=None)], W, H, T)
    svc.add("cam1", [person(480, 120, track=None)], W, H, T)  # someone passed by once
    picture = np.full((H, W, 3), 60, np.uint8)
    zone = [[0.5, 0.05], [0.95, 0.05], [0.95, 0.4], [0.5, 0.4]]
    image = decode(svc.render("cam1", 24, picture, zones=[zone], now=T)).astype(int)
    assert image.shape == (H, W, 3)
    # Busiest is red, at the feet; once is blue; elsewhere the picture is unchanged
    redness = image[..., 2] - image[..., 0]
    y, x = np.unravel_index(np.argmax(redness), redness.shape)
    assert abs(x - 165) <= 15 and abs(y - 305) <= 15 and redness[y, x] > 50
    faint = image[125, 485]
    assert faint[0] > faint[2] + 40
    assert np.abs(image[40, 40] - 60).max() < 8 and np.abs(image[200, 320] - 60).max() < 8
    # The zone's outline is drawn on top
    assert np.abs(image[int(0.4 * H), int(0.7 * W)] - ZONE).max() < 50


def test_render_without_activity_or_picture(tmp_path):
    svc = new_service(tmp_path)
    picture = np.full((720, 1280, 3), 90, np.uint8)
    image = decode(svc.render("cam1", 24, picture, now=T))
    assert image.shape == (540, 960, 3) and np.abs(image.astype(int) - 90).max() < 6  # unchanged, scaled down
    placeholder = decode(svc.render("cam1", 24, None, now=T))
    assert placeholder.shape == (360, 640, 3) and placeholder.mean() < 40


# ---- API --------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


def test_heatmap_api(client):
    heatmap_service.forget("cam1")
    summary = client.get("/api/cameras/cam1/heatmap/summary").json()
    assert summary["empty"] is True and summary["total"] == 0
    heatmap_service.add("cam1", [person(160, 300, track=None)] * 3, W, H, time.time())
    summary = client.get("/api/cameras/cam1/heatmap/summary?hours=168").json()
    assert summary["empty"] is False and summary["total"] == 3 and summary["hours"] == 168

    r = client.get("/api/cameras/cam1/heatmap.jpg?hours=24")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store" and decode(r.content) is not None
    assert client.get("/api/cameras/nope/heatmap.jpg").status_code == 404
    assert client.get("/api/cameras/nope/heatmap/summary").status_code == 404
    assert client.get("/api/cameras/cam1/heatmap.jpg?hours=0").status_code == 422


def test_removing_a_camera_deletes_its_heatmap(client):
    cam = client.post("/api/cameras", json={"name": "Shed", "source": "none"}).json()
    heatmap_service.add(cam["id"], [person(160, 300, track=None)], W, H, time.time())
    assert client.get(f"/api/cameras/{cam['id']}/heatmap/summary").json()["total"] == 1
    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 200
    assert heatmap_service.grid(cam["id"], 24).sum() == 0
    assert not (heatmap_service.directory / f"{cam['id']}.json").exists()


def test_briefing_api(client):
    r = client.post("/api/briefing/refresh")
    assert r.status_code == 200 and r.json()["enabled"] is True
    deadline = time.time() + 20
    while (briefing := client.get("/api/briefing").json())["generating"] and time.time() < deadline:
        time.sleep(0.1)
    # The language model points at a closed port in tests, so the briefing comes from the template
    assert briefing["source"] == "template" and briefing["text"] and briefing["generated_at"]
    assert briefing["period"]["hours"] == 24 and "incidents" in briefing["facts"]["counts"]

    settings = client.patch("/api/settings", json={"briefing": {"time": "07:15", "send": True}}).json()
    assert settings["briefing"] == {"enabled": True, "time": "07:15", "send": True}
    assert client.patch("/api/settings", json={"briefing": {"time": "7:15"}}).status_code == 422
    client.patch("/api/settings", json={"briefing": {"time": "08:00", "send": False}})
