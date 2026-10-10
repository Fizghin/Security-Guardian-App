import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main import app
from models.domain import Detection
from services import floorplan
from services.floorplan import CalibrationError, calibrate, project
from services.property_map import HANDOFF_SECONDS, PropertyMap, property_map
from services.settings_service import CameraConfig, MapSettings, Settings, SettingsError, SettingsService

MPP = 0.05  # 20 pixels per metre
# A camera looking at the ground: the top of its picture is far away (wide on the map), the bottom near
PERSPECTIVE = np.array([[400.0, 100.0, 100.0], [0.0, 300.0, 50.0], [0.0, 1.5, 1.0]])


def pairs_for(matrix, picture_points):
    out = []
    for x, y in picture_points:
        mx, my, w = matrix @ [x, y, 1.0]
        out.append([x, y, mx / w, my / w])
    return out


SPOTS = [(0.1, 0.2), (0.9, 0.2), (0.9, 0.9), (0.1, 0.9), (0.5, 0.5), (0.3, 0.7)]


# ---- calibration --------------------------------------------------------------------------
def test_homography_from_known_points():
    cal = calibrate(pairs_for(PERSPECTIVE, SPOTS), MPP)
    assert cal.error < 0.01 and not cal.outliers and not cal.exact
    for x, y in [(0.25, 0.4), (0.7, 0.95), (0.5, 0.05)]:
        mx, my, w = PERSPECTIVE @ [x, y, 1.0]
        assert project(cal, x, y) == pytest.approx((mx / w, my / w), abs=0.01)


def test_four_pairs_fit_exactly_so_the_error_says_nothing():
    cal = calibrate(pairs_for(PERSPECTIVE, SPOTS[:4]), MPP)
    assert cal.exact and cal.error < 0.01


def test_error_is_in_metres_and_a_pair_that_does_not_fit_is_left_out():
    pairs = pairs_for(PERSPECTIVE, SPOTS)
    rng = np.random.default_rng(1)
    for p in pairs:  # marking spots by hand is about 5 cm off
        p[2] += rng.normal(0, 1)
        p[3] += rng.normal(0, 1)
    pairs.append([0.7, 0.3, *pairs_for(PERSPECTIVE, [(0.7, 0.3)])[0][2:]])
    pairs[-1][2] += 60  # 3 m off
    cal = calibrate(pairs, MPP)
    assert cal.outliers == [6]
    assert cal.errors[6] == pytest.approx(3.0, abs=0.3)
    assert 0.01 < cal.error < 0.15


def test_ground_area_and_the_horizon():
    cal = calibrate(pairs_for(PERSPECTIVE, SPOTS), MPP)
    assert len(cal.ground) == 4  # the whole picture is ground: its farthest point is within reach
    assert cal.at == pytest.approx(project(cal, 0.5, 1.0))
    # A camera whose picture reaches the horizon: the far part is cut off and not projected
    tilted = np.array([[400.0, 100.0, 100.0], [0.0, 300.0, -100.0], [0.0, 3.0, -0.6]])
    cal = calibrate(pairs_for(tilted, [(0.1, 0.5), (0.9, 0.5), (0.9, 0.95), (0.1, 0.95), (0.5, 0.7)]), MPP)
    assert project(cal, 0.5, 0.05) is None
    assert project(cal, 0.5, 0.8) is not None
    assert len(cal.ground) == 4 and all(np.isfinite(cal.ground).ravel())


@pytest.mark.parametrize("pairs, message", [
    (pairs_for(PERSPECTIVE, SPOTS[:3]), "4 to 16"),
    (pairs_for(PERSPECTIVE, [(0.1, 0.2), (0.1, 0.2), (0.9, 0.9), (0.1, 0.9)]), "same place on the camera picture"),
    (pairs_for(PERSPECTIVE, [(0.1, 0.5), (0.5, 0.5), (0.9, 0.5), (0.5, 0.9)]), "almost on one line"),
    (pairs_for(PERSPECTIVE, [(0.1, 0.1), (0.3, 0.3), (0.6, 0.6), (0.9, 0.9), (0.5, 0.501)]), "almost on one line"),
    # left and right swapped on the map
    ([[x, y, 1000 - mx, my] for x, y, mx, my in pairs_for(PERSPECTIVE, SPOTS)], "mirror-image"),
])
def test_degenerate_or_mirrored_mappings_are_rejected(pairs, message):
    with pytest.raises(CalibrationError, match=message):
        calibrate(pairs, MPP)


def test_map_points_in_settings_are_validated(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    good = pairs_for(PERSPECTIVE, SPOTS[:4])
    assert svc.update_camera("cam1", {"map_points": good}).map_points[0] == [0.1, 0.2, round(good[0][2], 1),
                                                                            round(good[0][3], 1)]
    for bad in (good[:3], [[0.1, 0.2, 5]] * 4, [[1.2, 0.2, 5, 5]] + good[1:], [[0.1, 0.2, -5, 5]] + good[1:]):
        with pytest.raises(SettingsError):
            svc.update_camera("cam1", {"map_points": bad})
    with pytest.raises(SettingsError):
        svc.update({"map": {"image": "../settings.json"}})
    with pytest.raises(SettingsError):
        svc.update({"map": {"scale_line": [1, 2, 3]}})
    assert SettingsService(tmp_path / "settings.json").get().camera("cam1").map_points == svc.get().camera("cam1").map_points


# ---- the live map with a fake clock -------------------------------------------------------
# Both cameras see the ground the same way: picture (x, y) -> map (800x, 500y) pixels = (40x, 25y) metres
FLAT = [[0.1, 0.1, 80, 50], [0.9, 0.1, 720, 50], [0.9, 0.9, 720, 450], [0.1, 0.9, 80, 450]]


class FakeSettings:
    def __init__(self, a=FLAT, b=FLAT):
        self.value = Settings(map=MapSettings(image="grid", width=800, height=500, metres_per_px=MPP),
                              cameras=[CameraConfig(id="a", name="Drive", map_points=a),
                                       CameraConfig(id="b", name="Porch", map_points=b)])

    def get(self):
        return self.value


def seen(x, y, track, status="pending", name=None, visitor=None):
    """A person standing at (x, y) metres, in a 1000 x 1000 picture."""
    fx, fy = x / 40 * 1000, y / 25 * 1000
    return Detection(confidence=0.9, bbox=[fx - 40, fy - 300, fx + 40, fy], track_id=track, status=status,
                     identity=name, known=bool(name), visitor_id=visitor)


def people(m, now):
    return m.snapshot(now)["people"]


def test_foot_point_goes_through_the_calibration():
    m = PropertyMap(FakeSettings(a=pairs_for(PERSPECTIVE, SPOTS)))
    d = Detection(confidence=0.9, bbox=[400, 100, 600, 900], track_id=1, status="pending")  # feet at (0.5, 0.9)
    m.observe("a", [d], 1000, 1000, 100.0)
    (p,) = people(m, 100.0)
    mx, my, w = PERSPECTIVE @ [0.5, 0.9, 1.0]
    assert (p["x"], p["y"]) == pytest.approx((mx / w, my / w), abs=0.2)
    assert p["cameras"] == ["Drive"] and p["status"] == "pending" and p["label"] == "Checking"
    # a test intrusion isn't anybody
    m.observe("b", [Detection(confidence=0.99, bbox=[0, 0, 500, 500], track_id=5, simulated=True)], 1000, 1000, 100.1)
    assert len(people(m, 100.1)) == 1


def test_smoothing_and_speed():
    m = PropertyMap(FakeSettings())
    rng = np.random.default_rng(2)
    t = 100.0
    while t < 108:
        x = 5 + 1.2 * (t - 100)  # walking at 1.2 m/s
        m.observe("a", [seen(x + rng.normal(0, 0.15), 10 + rng.normal(0, 0.15), 1)], 1000, 1000, t)
        t += 0.4
    (p,) = people(m, t - 0.4)
    assert p["speed"] == pytest.approx(1.2, abs=0.25)
    assert p["x"] * MPP == pytest.approx(5 + 1.2 * (t - 0.4 - 100), abs=0.4)
    assert p["y"] * MPP == pytest.approx(10, abs=0.3)
    assert len(p["trail"]) >= 10 and p["trail"][0][2] < p["trail"][-1][2]
    # standing still slows down to nothing
    for _ in range(15):
        m.observe("a", [seen(15, 10, 1)], 1000, 1000, t)
        t += 0.4
    assert people(m, t)[0]["speed"] < 0.2


def test_two_cameras_seeing_one_person_at_the_same_moment():
    m = PropertyMap(FakeSettings())
    m.observe("a", [seen(10, 10, 1)], 1000, 1000, 100.0)
    m.observe("b", [seen(10.8, 10.3, 7)], 1000, 1000, 100.3)
    (p,) = people(m, 100.3)
    assert p["cameras"] == ["Drive", "Porch"]
    assert p["x"] * MPP == pytest.approx(10.4, abs=0.3)
    # and they stay one while both cameras follow them
    for i in range(1, 10):
        m.observe("a", [seen(10 + 0.3 * i, 10, 1)], 1000, 1000, 100 + 0.4 * i)
        m.observe("b", [seen(10.5 + 0.3 * i, 10.2, 7)], 1000, 1000, 100.2 + 0.4 * i)
    assert [q["id"] for q in people(m, 104)] == [p["id"]]


def test_people_apart_or_not_at_the_same_moment_stay_apart():
    m = PropertyMap(FakeSettings())
    m.observe("a", [seen(10, 10, 1)], 1000, 1000, 100.0)
    m.observe("b", [seen(12, 10, 7)], 1000, 1000, 100.2)  # 2 m apart
    assert len(people(m, 100.2)) == 2
    # one camera sees two people close together: still two
    m2 = PropertyMap(FakeSettings())
    m2.observe("a", [seen(10, 10, 1), seen(10.5, 10, 2)], 1000, 1000, 100.0)
    assert len(people(m2, 100.0)) == 2


def test_joined_people_who_walk_apart_split_and_the_newcomer_leaves():
    m = PropertyMap(FakeSettings())
    m.observe("a", [seen(10, 10, 1)], 1000, 1000, 100.0)
    m.observe("b", [seen(10.5, 10, 7)], 1000, 1000, 100.1)
    (p,) = people(m, 100.1)
    m.observe("a", [seen(10, 10, 1)], 1000, 1000, 100.4)
    m.observe("b", [seen(14, 10, 7)], 1000, 1000, 100.5)  # jumps 3.5 m away: someone else
    ids = {q["cameras"][0]: q["id"] for q in people(m, 100.5)}
    assert ids["Drive"] == p["id"] and ids["Porch"] != p["id"]


def test_handoff_at_walking_speed_keeps_the_id():
    m = PropertyMap(FakeSettings())
    for i in range(5):
        m.observe("a", [seen(8 + 0.5 * i, 10, 1)], 1000, 1000, 100 + 0.4 * i)  # walks out of camera a's view
    (p,) = people(m, 101.6)
    # 2.5 s later camera b sees someone 4 m on: 1.6 m/s, so the same person
    m.observe("b", [seen(14, 10, 3)], 1000, 1000, 104.1)
    (q,) = people(m, 104.1)
    assert q["id"] == p["id"] and q["cameras"] == ["Porch"]
    # someone appearing 20 m away a second later can't have walked there
    m.observe("b", [], 1000, 1000, 105.0)
    m.observe("a", [seen(34, 10, 9)], 1000, 1000, 107.0)
    assert people(m, 107.0)[0]["id"] != p["id"]


def test_ids_last_only_briefly():
    m = PropertyMap(FakeSettings())
    m.observe("a", [seen(10, 10, 1)], 1000, 1000, 100.0)
    (p,) = people(m, 100.0)
    m.observe("b", [seen(10, 10, 4)], 1000, 1000, 100 + HANDOFF_SECONDS + 2.5)
    assert people(m, 100 + HANDOFF_SECONDS + 2.5)[0]["id"] != p["id"]


def test_a_recognised_face_wins():
    m = PropertyMap(FakeSettings())
    m.observe("a", [seen(10, 10, 1, "known", "Sam")], 1000, 1000, 100.0)
    m.observe("b", [seen(10.5, 10, 7, "unknown")], 1000, 1000, 100.2)
    (p,) = people(m, 100.2)
    assert (p["status"], p["label"], p["identified_on"]) == ("known", "Sam", "Drive")
    assert {v["camera"]: v["label"] for v in p["views"]} == {"Drive": "Sam", "Porch": "Unknown"}

    # faces of two different people are never joined, however close
    m2 = PropertyMap(FakeSettings())
    m2.observe("a", [seen(10, 10, 1, "known", "Sam")], 1000, 1000, 100.0)
    m2.observe("b", [seen(10.3, 10, 7, "known", "Alex")], 1000, 1000, 100.1)
    assert sorted(q["label"] for q in people(m2, 100.1)) == ["Alex", "Sam"]

    # Sam is lost, then recognised far away, too far to have walked: still Sam's id
    m.observe("a", [], 1000, 1000, 103.0)
    m.observe("b", [], 1000, 1000, 103.0)
    m.observe("b", [seen(35, 20, 8, "known", "Sam")], 1000, 1000, 104.0)
    (q,) = people(m, 104.0)
    assert q["id"] == p["id"] and q["label"] == "Sam"


def test_a_face_seen_in_two_places_does_not_make_people_jump():
    # Two people both recognised as visitor 3 (say, twins, or one of them is a photo)
    m = PropertyMap(FakeSettings())
    for i in range(3):
        m.observe("a", [seen(5, 5, 1, "unknown", visitor=3)], 1000, 1000, 100 + 0.4 * i)
        m.observe("b", [seen(30 + 0.2 * i, 20, 2, "unknown", visitor=3)], 1000, 1000, 100.2 + 0.4 * i)
    ids = {q["cameras"][0]: q["id"] for q in people(m, 101.0)}
    # camera a misses its one for a second while camera b's tracker starts a new track for the other
    m.observe("b", [seen(30.6, 20, 2, "unknown", visitor=3)], 1000, 1000, 101.4)
    m.observe("b", [seen(30.8, 20, 7, "unknown", visitor=3)], 1000, 1000, 101.8)
    assert {q["id"]: q["cameras"] for q in people(m, 101.8)} == {ids["Drive"]: ["Drive"], ids["Porch"]: ["Porch"]}


def test_a_face_recognised_later_picks_up_the_lost_id():
    m = PropertyMap(FakeSettings())
    m.observe("a", [seen(5, 5, 1, "known", "Sam")], 1000, 1000, 100.0)
    (p,) = people(m, 100.0)
    m.observe("b", [seen(35, 20, 2)], 1000, 1000, 103.0)  # too far to be Sam, until the face is seen
    assert people(m, 103.0)[0]["id"] != p["id"]
    m.observe("b", [seen(35, 20, 2, "known", "Sam")], 1000, 1000, 103.4)
    (q,) = people(m, 103.4)
    assert q["id"] == p["id"]


def test_visitors_and_strangers():
    m = PropertyMap(FakeSettings())
    d = seen(10, 10, 1, "unknown", visitor=12)
    m.observe("a", [d, seen(30, 10, 2, "unknown")], 1000, 1000, 100.0)
    labels = {q["label"]: q["status"] for q in people(m, 100.0)}
    assert labels == {"Visitor 12": "unknown", "Unknown": "unknown"}


def test_history_buffer():
    m = PropertyMap(FakeSettings())
    t = 0.0
    while t <= 120:
        m.observe("a", [seen(5 + t / 20, 10, 1)], 1000, 1000, 1000 + t)
        t += 0.5
    recent = m.history(1, 1120)
    assert 55 <= len(recent) <= 62 and all(s[0] >= 1060 for s in recent)
    assert len(m.history(10, 1120)) >= 118
    when, pid, x, y, status, label = recent[-1]
    assert (pid, status, label) == (1, "pending", "Checking") and x == pytest.approx(11 / MPP, abs=10)


def test_a_new_calibration_or_floorplan_starts_afresh():
    fake = FakeSettings()
    m = PropertyMap(fake)
    m.observe("a", [seen(10, 10, 1)], 1000, 1000, 100.0)
    assert people(m, 100.0)
    fake.value = fake.value.model_copy(update={"cameras": [fake.value.cameras[0].model_copy(update={"map_points": []}),
                                                           fake.value.cameras[1]]})
    assert people(m, 100.1) == []
    assert m.calibrations()["a"] == (None, None)
    m.observe("a", [seen(10, 10, 1)], 1000, 1000, 100.2)
    assert people(m, 100.2) == []


# ---- API and WebSocket --------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def png(w=400, h=300) -> bytes:
    img = np.full((h, w, 3), 255, np.uint8)
    cv2.rectangle(img, (40, 40), (w - 40, h - 40), (0, 0, 0), 3)
    return cv2.imencode(".png", img)[1].tobytes()


def test_map_setup_through_the_api(client):
    r = client.post("/api/map/grid", json={"width_m": 30, "height_m": 20})
    assert r.status_code == 200
    body = r.json()
    assert (body["image"], body["width"], body["height"], body["metres_per_px"]) == ("grid", 600, 400, 0.05)

    good = pairs_for(PERSPECTIVE, SPOTS)
    r = client.post("/api/map/cameras/cam1/check", json={"points": good})
    assert r.status_code == 200 and r.json()["error"] < 0.05 and len(r.json()["field"]) == 4
    mirrored = [[x, y, 600 - mx, my] for x, y, mx, my in good]
    r = client.post("/api/map/cameras/cam1/check", json={"points": mirrored})
    assert r.status_code == 422 and "mirror" in r.json()["detail"]
    r = client.post("/api/map/cameras/cam1/check", json={"points": [[0.1, 0.1, 900, 10]] + good[1:]})
    assert r.status_code == 422 and "inside" in r.json()["detail"]
    assert client.post("/api/map/cameras/nope/check", json={"points": good}).status_code == 404

    r = client.put("/api/map/cameras/cam1", json={"points": good})
    cam = next(c for c in r.json()["cameras"] if c["id"] == "cam1")
    assert len(cam["points"]) == 6 and cam["calibration"]["error"] < 0.05

    r = client.post("/api/map/scale", json={"line": [0, 0, 100, 0], "metres": 10})
    assert r.json()["metres_per_px"] == pytest.approx(0.1) and r.json()["scale_line"] == [0, 0, 100, 0, 10]
    assert client.post("/api/map/scale", json={"line": [0, 0, 2, 0], "metres": 10}).status_code == 422
    # the map has its own endpoints; settings can't change it
    client.patch("/api/settings", json={"map": {"image": "", "width": 5}})
    assert client.get("/api/map").json()["width"] == 600

    # a floorplan picture replaces the grid and the cameras' points, which were on the grid
    r = client.post("/api/map/picture", files={"file": ("plan.png", png(), "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["image"].endswith(".png") and (body["width"], body["height"]) == (400, 300)
    assert body["metres_per_px"] is None and all(not c["points"] for c in body["cameras"])
    pic = client.get(body["picture"])
    assert pic.status_code == 200 and pic.headers["content-type"] == "image/png"
    assert len(list(floorplan.MAP_DIR.glob("floorplan-*"))) == 1
    assert client.post("/api/map/cameras/cam1/check", json={"points": good}).status_code == 409  # no scale yet
    r = client.post("/api/map/picture", files={"file": ("plan.png", b"not a picture", "image/png")})
    assert r.status_code == 422 and "PNG or JPEG" in r.json()["detail"]

    r = client.delete("/api/map")
    assert r.json()["image"] == "" and not list(floorplan.MAP_DIR.glob("floorplan-*"))


def test_map_socket_and_history(client):
    client.post("/api/map/grid", json={"width_m": 40, "height_m": 25})
    client.put("/api/map/cameras/cam1", json={"points": FLAT})
    now = time.time()
    property_map.observe("cam1", [seen(10, 10, 41)], 1000, 1000, now)
    with client.websocket_connect("/ws/map") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "map"
    (p,) = frame["people"]
    assert p["x"] == pytest.approx(200, abs=2) and p["y"] == pytest.approx(200, abs=2)
    assert p["cameras"] == ["Camera 1"] and frame["cameras"][0]["id"] == "cam1"
    samples = client.get("/api/map/history?minutes=10").json()["samples"]
    assert samples and samples[-1][1] == p["id"]
    assert client.get("/api/map/history?minutes=100").status_code == 422
    # other websites can't follow people around
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/map", headers={"origin": "http://example.com"}) as ws:
            ws.receive_json()
    client.delete("/api/map")
