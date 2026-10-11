import time
from datetime import timedelta

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

import api as api_module
from models import database
from models.database import SessionLocal, Visitor, VisitorFace, VisitorSighting, utcnow
from models.domain import Detection
from services.event_service import EventService
from services.brain_service import CameraBrain
from services.camera_service import CameraUnit, draw_overlay
from services.face_service import FaceError
from services.settings_service import CameraConfig, SettingsService
from services.tracker import FaceEvidence
from services.visitor_service import LOOK_AGAIN, MAX_FACES, NEW_AFTER, SEND_EVERY, TRACK_END, VisitorService, when

T = 0.36  # the insider match threshold
DAY = 86400.0


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


SAM, SAM_AGAIN, ANA = unit([1, 0, 0, 0]), unit([0.95, 0.1, 0.05, 0]), unit([0, 1, 0, 0])


class FakeFaces:
    """Insider photos as embeddings; add_photo enrols whatever it is given unless told to refuse."""

    def __init__(self):
        self.insiders: dict[str, np.ndarray] = {}
        self.added: list[tuple[str, bytes]] = []
        self.refuse = False

    def closest_insider(self, feat):
        if not self.insiders:
            return None, 0.0
        best, name = max((float(np.dot(feat, f)), n) for n, f in self.insiders.items())
        return name, best

    def add_photo(self, name, data):
        if self.refuse:
            raise FaceError("Face is too small. Use a closer photo")
        self.added.append((name, data))
        return {"name": name, "file": "x.jpg", "warning": None}


@pytest.fixture
def svc(tmp_path):
    database.init_db()
    settings = SettingsService(tmp_path / "settings.json")
    events = EventService(snapshot_dir=tmp_path / "snapshots")
    service = VisitorService(tmp_path / "visitors", settings=settings, faces=FakeFaces(), events=events)
    service.dir.mkdir()
    service.forget_all()  # the test database is shared
    return service


def stranger(track_id, status="unknown"):
    return Detection(confidence=0.9, bbox=[0, 0, 100, 250], track_id=track_id, status=status)


def look(feature, quality=0.8, shade=128):
    crop = np.full((120, 100, 3), shade, np.uint8)
    return FaceEvidence(True, True, None, 0.1, feature=feature, crop=crop, quality=quality)


def visit(svc, track_id, feature, start, seconds=6.0, quality=0.8, camera="cam1", event_id=None, status="unknown"):
    """Someone in view of a camera from `start` for `seconds`, facing it, who then leaves."""
    t, d = start, None
    while t <= start + seconds:
        d = stranger(track_id, status)
        svc.annotate(camera, [d], [look(feature, quality)], t, T)
        svc.record(camera, "Front door", [d], t, event_id)
        t += 0.5
    svc.expire(t + TRACK_END + 1)
    svc.process_pending()
    return d


def visitor_of(feature) -> int:
    """The visitor whose reference embedding is closest to this face."""
    return max(rows(Visitor), key=lambda v: float(np.dot(np.frombuffer(v.embedding, np.float32), feature))).id


def rows(model):
    db = SessionLocal()
    try:
        return db.query(model).all()
    finally:
        db.close()


# ---- matching and visits --------------------------------------------------------------
def test_same_person_is_one_visitor_and_different_people_are_two(svc):
    visit(svc, 1, SAM, 1000)
    visit(svc, 2, ANA, 1000, camera="cam2")
    visit(svc, 3, SAM_AGAIN, 1000 + DAY)
    items = {i["id"]: i for i in svc.list()}
    assert len(items) == 2
    sam = next(i for i in items.values() if i["visits"] == 2)
    assert sam["name"] == f"Visitor {sam['id']}" and sam["cameras"] == ["Front door"]
    assert [i["visits"] for i in svc.list()] == [2, 1], "repeat visitors first"
    assert [i["id"] for i in svc.list(repeat=True)] == [sam["id"]]


def test_a_face_close_to_two_visitors_counts_as_no_match(svc):
    visit(svc, 1, SAM, 1000)
    visit(svc, 2, ANA, 1000)
    d = visit(svc, 3, unit([1, 1, 0, 0]), 2000)  # halfway between them
    assert d.visitor_id is None and d.seen_before is None
    assert len(svc.list()) == 3, "remembered as someone new rather than guessed"


def test_returning_visitor_is_marked_before_the_brain_sees_them(svc):
    visit(svc, 1, SAM, 1000)
    first = svc.list()[0]
    d = stranger(7)
    svc.annotate("cam1", [d], [look(SAM_AGAIN)], 1000 + 3 * DAY, T)
    assert d.visitor_id == first["id"]
    assert d.seen_before.startswith(f"Visitor {first['id']}, seen before: 1 visit, last ")
    assert d.visitor_label is None
    svc.update(first["id"], {"label": "  Parcel   courier "})
    svc.annotate("cam1", [d], [look(SAM_AGAIN)], 1000 + 3 * DAY + 0.5, T)
    assert d.visitor_label == "Parcel courier", "the live picture shows the name given to them"


def test_people_still_being_identified_are_not_marked(svc):
    visit(svc, 1, SAM, 1000)
    d = stranger(7, "pending")
    svc.annotate("cam1", [d], [look(SAM)], 5000, T)
    assert d.visitor_id is None


@pytest.mark.parametrize("gap_minutes, away_minutes, visits", [(30, 10, 1), (30, 40, 2), (0, 1, 2)])
def test_a_visit_is_a_sighting_after_the_gap(svc, gap_minutes, away_minutes, visits):
    svc.settings.update({"detection": {"visit_gap_minutes": gap_minutes}})
    visit(svc, 1, SAM, 1000, seconds=60)
    visit(svc, 2, SAM_AGAIN, 1060 + away_minutes * 60)
    [item] = svc.list()
    assert item["visits"] == visits
    detail = svc.get(item["id"])
    assert len(detail["sightings"]) == 2 and detail["sightings"][0]["new_visit"] is (visits == 2)
    assert detail["last_seen"] > detail["first_seen"]


def test_saving_happens_off_the_camera_thread_and_is_rate_limited(svc):
    visit(svc, 1, SAM, 1000)
    t, saves = 2000.0, 0
    for i in range(40):  # 20 s in view, with a better look at the face every time
        d = stranger(2)
        svc.annotate("cam1", [d], [look(SAM_AGAIN, quality=0.2 + i / 100)], t, T)
        svc.record("cam1", "Front door", [d], t, None)
        if i == 0:
            assert len(rows(VisitorSighting)) == 1, "the camera thread only queues the work"
        saves += svc._queue.qsize()
        svc.process_pending()  # the background thread keeps up
        t += 0.5
    assert saves == 20 / SEND_EVERY
    assert len(rows(VisitorSighting)) == 2


def test_a_quick_passer_by_is_remembered_after_leaving(svc):
    d = stranger(1)
    svc.annotate("cam1", [d], [look(SAM)], 1000, T)
    svc.record("cam1", "Front door", [d], 1000, None)
    assert NEW_AFTER > 0 and svc._queue.qsize() == 0
    svc.expire(1000 + TRACK_END + 1)
    svc.process_pending()
    assert len(svc.list()) == 1


def test_event_picture_is_linked_and_hidden_after_the_log_is_cleared(svc):
    event_id = svc.events.log("DETECTION", "Unrecognised person detected", "LOW", camera="Front door",
                              snapshot=b"\xff\xd8 picture")
    visit(svc, 1, SAM, time.time(), event_id=event_id)
    [sighting] = svc.get(svc.list()[0]["id"])["sightings"]
    assert sighting["event"]["id"] == event_id and sighting["event"]["snapshot"].endswith("_detection.jpg")
    svc.events.clear()
    svc.events.log("PANIC", "Alarm raised", snapshot=b"other picture")  # may reuse the id
    assert svc.get(svc.list()[0]["id"])["sightings"][0]["event"] is None


# ---- faces ---------------------------------------------------------------------------------
def test_keeps_the_best_face_of_each_sighting_up_to_six(svc):
    for i in range(MAX_FACES + 2):
        visit(svc, i + 1, SAM, 1000 + i * DAY, quality=0.3 + i * 0.05)
    item = svc.list()[0]
    faces = sorted(rows(VisitorFace), key=lambda f: -f.quality)
    assert len(faces) == MAX_FACES and item["faces"] == [f.file for f in faces]
    assert faces[-1].quality == pytest.approx(0.3 + 2 * 0.05), "the two worst were dropped"
    files = {p.name for p in (svc.dir / str(item["id"])).iterdir()}
    assert files == set(item["faces"]), "dropped faces are deleted from disk"
    assert len({f.sighting_id for f in faces}) == MAX_FACES, "one face per sighting"


def test_a_better_look_replaces_the_sightings_face(svc):
    d = stranger(1)
    for t, q in ((1000, 0.3), (1003, 0.5), (1010, 0.9)):  # saved at 1003 and again at 1010
        svc.annotate("cam1", [d], [look(SAM, q)], t, T)
        svc.record("cam1", "Front door", [d], t, None)
        svc.process_pending()
    [face] = rows(VisitorFace)
    assert face.quality == pytest.approx(0.9)
    assert [p.name for p in (svc.dir / str(face.visitor_id)).iterdir()] == [face.file]
    img = cv2.imread(str(svc.dir / str(face.visitor_id) / face.file))
    assert min(img.shape[:2]) >= 224, "small crops are enlarged so they can be enrolled"


def test_reference_is_the_mean_of_the_faces(svc):
    visit(svc, 1, SAM, 1000)
    visit(svc, 2, SAM_AGAIN, 1000 + DAY)
    [visitor] = rows(Visitor)
    mean = unit(SAM + SAM_AGAIN)
    assert np.allclose(np.frombuffer(visitor.embedding, np.float32), mean, atol=1e-5)


def test_photos_by_rank_and_version(svc):
    visit(svc, 1, SAM, 1000, quality=0.4)
    visit(svc, 2, SAM, 1000 + DAY, quality=0.9)
    item = svc.list()[0]
    best = svc.photo_path(item["id"], 0, item["faces"][0])
    assert best.name == item["faces"][0]
    for n, version in ((5, ""), (-1, ""), (0, item["faces"][1]), (0, "../../guardian.db")):
        with pytest.raises(FileNotFoundError):
            svc.photo_path(item["id"], n, version)


# ---- insiders --------------------------------------------------------------------------------
def test_insiders_are_never_stored(svc):
    svc.faces.insiders = {"Carlo": ANA}
    visit(svc, 1, ANA, 1000)  # counted as unknown at first, but the face is an insider's
    d = stranger(2)
    svc.annotate("cam1", [d], [look(SAM)], 2000, T)
    d.status, d.identity = "known", "Sam"  # recognised once they came closer
    svc.annotate("cam1", [d], [look(SAM)], 2000.5, T)
    svc.expire(2010)
    svc.process_pending()
    assert svc.list() == []


def test_make_insider_enrols_the_best_faces_and_forgets_the_visitor(svc):
    visit(svc, 1, SAM, 1000, quality=0.5)
    visit(svc, 2, SAM, 1000 + DAY, quality=0.9)
    item = svc.list()[0]
    best = (svc.dir / str(item["id"]) / item["faces"][0]).read_bytes()
    result = svc.make_insider(item["id"], " Sam ")
    assert result == {"name": "Sam", "added": 2, "skipped": 0}
    assert svc.faces.added[0] == ("Sam", best), "best face first"
    assert svc.list() == [] and not (svc.dir / str(item["id"])).exists()
    assert "Added Visitor" in svc.events.query(limit=1)["items"][0]["description"]


def test_make_insider_keeps_the_visitor_when_no_photo_is_usable(svc):
    visit(svc, 1, SAM, 1000)
    svc.faces.refuse = True
    item = svc.list()[0]
    with pytest.raises(FaceError, match="too small"):
        svc.make_insider(item["id"], "Sam")
    with pytest.raises(FaceError):
        svc.make_insider(item["id"], "../..")
    assert len(svc.list()) == 1


# ---- forgetting -------------------------------------------------------------------------------
def test_forget_one_and_all(svc):
    visit(svc, 1, SAM, 1000)
    visit(svc, 2, ANA, 1000)
    sam = visitor_of(SAM)
    svc.forget(sam)
    assert len(svc.list()) == 1 and not (svc.dir / str(sam)).exists()
    assert not [f for f in rows(VisitorFace) if f.visitor_id == sam]
    with pytest.raises(KeyError):
        svc.forget(sam)
    assert svc.forget_all() == 1
    assert svc.list() == [] and rows(VisitorSighting) == [] and list(svc.dir.iterdir()) == []


def test_forgotten_visitor_still_in_view_is_not_remembered_again(svc):
    d = stranger(1)
    for t in (1000, 1000 + NEW_AFTER):
        svc.annotate("cam1", [d], [look(SAM)], t, T)
        svc.record("cam1", "Front door", [d], t, None)
    svc.process_pending()
    svc.forget(svc.list()[0]["id"])
    for t in (1010, 1020, 1030):
        svc.annotate("cam1", [d], [look(SAM, 0.9)], t, T)
        svc.record("cam1", "Front door", [d], t, None)
    svc.expire(1100)
    svc.process_pending()
    assert svc.list() == []


def test_prune_forgets_old_visitors_and_lookalikes_of_insiders(svc):
    visit(svc, 1, SAM, time.time())
    visit(svc, 2, ANA, time.time())
    (svc.dir / "999").mkdir()  # left behind, e.g. by a crash
    sam_id, ana_id = visitor_of(SAM), visitor_of(ANA)
    db = SessionLocal()
    db.get(Visitor, sam_id).last_seen = utcnow() - timedelta(days=100)
    db.commit()
    db.close()
    svc.settings.update({"detection": {"visitor_retention_days": 0}})
    assert svc.prune() == 0, "0 keeps visitors forever"
    assert not (svc.dir / "999").exists()
    svc.settings.update({"detection": {"visitor_retention_days": 90}})
    assert svc.prune() == 1
    assert [i["id"] for i in svc.list()] == [ana_id] and not (svc.dir / str(sam_id)).exists()
    svc.faces.insiders = {"Ana": ANA}  # added as an insider later
    assert svc.prune() == 1 and svc.list() == []


def test_visitors_are_matched_again_after_a_restart(svc, tmp_path):
    visit(svc, 1, SAM, 1000)
    again = VisitorService(svc.dir, settings=svc.settings, faces=svc.faces, events=svc.events)
    d = stranger(9)
    again.annotate("cam1", [d], [look(SAM_AGAIN)], 1000 + DAY, T)
    assert d.visitor_id == svc.list()[0]["id"]


def test_visitor_numbers_are_not_given_out_again(svc):
    visit(svc, 1, SAM, 1000)
    first = svc.list()[0]["id"]
    svc.forget_all()
    visit(svc, 2, SAM, 2000)
    assert svc.list()[0]["id"] > first


def test_last_seen_wording():
    now = time.mktime((2026, 10, 9, 12, 0, 0, 0, 0, -1))  # a Friday
    at = lambda days, h, m: time.mktime((2026, 10, 9 - days, h, m, 0, 0, 0, -1))  # noqa: E731
    assert when(at(0, 9, 5), now) == "today 09:05"
    assert when(at(1, 23, 10), now) == "yesterday 23:10"
    assert when(at(3, 23, 10), now) == "Tue 23:10"
    assert when(at(8, 7, 0), now) == "1 Oct 07:00"


# ---- the camera pipeline -------------------------------------------------------------------------
class People:
    error = None

    def __init__(self):
        self.boxes = [[100, 100, 300, 600]]

    def detect_persons(self, frame, confidence, min_height):
        return [Detection(confidence=0.9, bbox=list(box)) for box in self.boxes]

    def detect(self, frame, confidence, min_height, bags=False):
        return self.detect_persons(frame, confidence, min_height), []


class FaceModels:
    """Loaded, with no insiders enrolled; every person shows this face."""
    ready, active = True, False

    def __init__(self, feature):
        self.feature, self.kept = feature, []

    def analyze(self, frame, detections, threshold, keep_faces=False):
        self.kept.append(keep_faces)
        return [look(self.feature) if keep_faces else FaceEvidence() for _ in detections]


def camera(svc, feature):
    from test_brain import FakeAI, FakeEvents, FakeNotifier, FakeRecorder, FakeSpeaker
    unit = CameraUnit(CameraConfig(id="door", name="Front door", source="none"), svc.settings, detector=People(),
                      faces=FaceModels(feature), events=svc.events, visitors=svc)
    unit.brain = CameraBrain("door", unit.name, FakeSpeaker(), FakeRecorder(), settings=svc.settings, ai=FakeAI(),
                             notifier=FakeNotifier(), events=FakeEvents())
    return unit


def test_camera_says_a_returning_stranger_was_seen_before(svc):
    now = time.time()
    visit(svc, 1, SAM, now - DAY)
    unit = camera(svc, SAM_AGAIN)
    frame = np.zeros((720, 1280, 3), np.uint8)
    for t in (now, now + 0.5):  # two clear looks: a stranger, and no need to wait to identify them
        unit._detect(frame, t, svc.settings.get(), False)
    [(kind, text)] = [e for e in unit.brain.events.descriptions if e[0] == "DETECTION"]
    assert text.startswith(f"Unrecognised person detected (Visitor {visitor_of(SAM)}, seen before: 1 visit, last ")
    svc.update(visitor_of(SAM), {"label": "Courier"})
    unit._detect(frame, now + 1, svc.settings.get(), False)
    assert unit._detections[0].visitor_label == "Courier"
    draw_overlay(frame, unit._detections, "Front door", True)  # labelled, still drawn as a stranger
    assert tuple(frame[300, 100]) == (40, 40, 220), "red"


def test_people_already_recognised_are_looked_at_less_often(svc):
    # Looking for faces is what costs the camera frames; with no insiders it is only needed for visitors.
    now = time.time()
    visit(svc, 1, SAM, now - DAY)
    unit = camera(svc, SAM_AGAIN)
    frame = np.zeros((720, 1280, 3), np.uint8)
    for i in range(25):  # 10 s in view, a detection every 0.4 s
        unit._detect(frame, now + i * 0.4, svc.settings.get(), False)
    assert len(unit.faces.kept) == 2 + 1, f"recognised from two looks, then looked at every {LOOK_AGAIN:.0f} s"
    unit.detector.boxes.append([700, 100, 900, 600])  # someone else arrives
    unit._detect(frame, now + 10, svc.settings.get(), False)
    assert len(unit.faces.kept) == 4


def test_camera_remembers_nobody_while_disarmed(svc):
    svc.settings.update({"armed": False})
    unit = camera(svc, SAM)
    for t in (1000, 1001, 1003, 1010):
        unit._detect(np.zeros((720, 1280, 3), np.uint8), t, svc.settings.get(), False)
    assert unit.faces.kept == [] and svc._tracks == {}, "no face analysis is needed with no insiders"
    svc.settings.update({"armed": True, "detection": {"remember_visitors": False}})
    unit._detect(np.zeros((720, 1280, 3), np.uint8), 1020, svc.settings.get(), False)
    assert svc._tracks == {}


# ---- storage --------------------------------------------------------------------------------------
def test_database_from_an_older_version_gets_the_visitor_tables(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE security_events (id INTEGER PRIMARY KEY, timestamp DATETIME, "
                          "event_type VARCHAR, description VARCHAR, severity VARCHAR)"))
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    tables = set(inspect(engine).get_table_names())
    assert {"visitors", "visitor_sightings", "visitor_faces"} <= tables
    columns = {c["name"] for c in inspect(engine).get_columns("visitor_sightings")}
    assert {"event_id", "event_picture", "new_visit"} <= columns


# ---- API ---------------------------------------------------------------------------------------------
@pytest.fixture
def client(svc, monkeypatch):
    monkeypatch.setattr(api_module, "visitor_service", svc)
    app = FastAPI()
    app.include_router(api_module.router)
    return TestClient(app)


def test_api(client, svc):
    visit(svc, 1, SAM, 1000, quality=0.5)
    visit(svc, 2, SAM_AGAIN, 1000 + DAY, quality=0.7)
    visit(svc, 3, ANA, 5000)
    listing = client.get("/api/visitors").json()
    assert listing["enabled"] is False, "face recognition is off in the test settings"
    assert listing["retention_days"] == 90
    assert [i["visits"] for i in listing["items"]] == [2, 1]
    repeat = client.get("/api/visitors", params={"repeat": True}).json()["items"]
    assert len(repeat) == 1
    vid = repeat[0]["id"]

    detail = client.get(f"/api/visitors/{vid}").json()
    assert len(detail["sightings"]) == 2 and detail["sightings"][0]["camera"] == "Front door"
    photo = client.get(f"/api/visitors/{vid}/photos/0.jpg", params={"v": detail["faces"][0]})
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    assert photo.headers["cache-control"].startswith("private")
    assert client.get(f"/api/visitors/{vid}/photos/7.jpg").status_code == 404
    assert client.get(f"/api/visitors/{vid}/photos/0.jpg", params={"v": "../../x.jpg"}).status_code == 404
    assert client.get("/api/visitors/../../guardian.db").status_code == 404
    assert client.get("/api/visitors/abc").status_code == 422

    renamed = client.patch(f"/api/visitors/{vid}", json={"label": "Courier", "note": "Blue van"}).json()
    assert renamed["name"] == "Courier" and renamed["note"] == "Blue van"
    assert client.get("/api/visitors", params={"search": "van"}).json()["items"][0]["id"] == vid
    assert client.patch(f"/api/visitors/{vid}", json={"label": None}).json()["name"] == f"Visitor {vid}"
    assert client.patch("/api/visitors/999999", json={"label": "x"}).status_code == 404

    r = client.post(f"/api/visitors/{vid}/make-insider", json={"name": "Sam"})
    assert r.status_code == 200 and r.json()["added"] == 2
    assert client.get(f"/api/visitors/{vid}").status_code == 404
    assert client.post(f"/api/visitors/{vid}/make-insider", json={"name": "Sam"}).status_code == 404

    other = client.get("/api/visitors").json()["items"][0]["id"]
    svc.faces.refuse = True
    r = client.post(f"/api/visitors/{other}/make-insider", json={"name": "Ana"})
    assert r.status_code == 409 and "too small" in r.json()["detail"]
    assert client.delete(f"/api/visitors/{other}").json() == {"ok": True}
    assert client.delete(f"/api/visitors/{other}").status_code == 404
    visit(svc, 4, ANA, 9000)
    assert client.delete("/api/visitors").json() == {"deleted": 1}
    assert client.get("/api/visitors").json()["items"] == []
