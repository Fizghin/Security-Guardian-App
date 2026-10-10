import time
from datetime import timedelta
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

import api as api_module
from models import database
from models.database import SecurityEvent, SessionLocal
from models.domain import Detection
from services.brain_service import CameraBrain
from services.camera_service import DIM, CameraUnit, draw_overlay
from services.event_service import EventService
from services.face_service import LEARNED_PREFIX, FaceService
from services.learning_service import (DISMISS_FOR, LEARN_EVERY, MAX_LEARNED, SUGGEST_AFTER, LearningError,
                                       LearningService, event_details)
from services.settings_service import CameraConfig, SettingsService
from services.tracker import FaceEvidence, Track, Tracker
from test_brain import FakeAI, FakeNotifier, FakeRecorder, FakeSpeaker

T = 0.36  # the insider match threshold
W, H = 1280, 720
BOX = [500, 150, 700, 650]  # someone standing in the middle of a 1280x720 picture
SPOT = [round(BOX[0] / W, 3), round(BOX[1] / H, 3), round(BOX[2] / W, 3), round(BOX[3] / H, 3)]


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


ALICE, NEW_LOOK, SAME_LOOK = unit([1, 0, 0, 0]), unit([0.8, 0.6, 0, 0]), unit([0.99, 0.1, 0, 0])


@pytest.fixture
def env(tmp_path):
    database.init_db()
    settings = SettingsService(tmp_path / "settings.json")
    settings.update({"cameras": [{"id": "garden", "name": "Garden", "source": "none"}],
                     "detection": {"remember_visitors": False}})
    events = EventService(snapshot_dir=tmp_path / "snapshots")
    faces = FaceService(tmp_path / "faces", tmp_path / "models")
    faces.faces_dir.mkdir()
    learning = LearningService(tmp_path / "learning", settings=settings, faces=faces, events=events)
    return SimpleNamespace(settings=settings, events=events, faces=faces, learning=learning, tmp=tmp_path)


def person(box=SPOT, status="unknown", identity=None, track_id=1, simulated=False):
    p = {"box": list(box), "confidence": 0.9, "status": status, "identity": identity, "track_id": track_id,
         "face_visible": False, "match_score": None}
    if simulated:
        p["simulated"] = True
    return p


def event(env, *people, event_type="DETECTION", insider=None, camera_id="garden"):
    details = {"camera_id": camera_id, "people": list(people) or [person()]}
    if insider:
        details["insider"] = insider
    return env.events.log(event_type, "Test", "LOW", camera="Garden", details=details)


def stored(event_id):
    db = SessionLocal()
    try:
        return db.get(SecurityEvent, event_id).to_dict()
    finally:
        db.close()


def events_after(marker, event_type=None):
    db = SessionLocal()
    try:
        q = db.query(SecurityEvent).filter(SecurityEvent.id > marker, SecurityEvent.camera == "Garden")
        return [r.to_dict() for r in q if event_type is None or r.event_type == event_type]
    finally:
        db.close()


def last_id(env):
    return env.events.log("TEST", "marker", camera="Elsewhere")


# ---- the camera pipeline -----------------------------------------------------------------------
class People:
    error = None

    def __init__(self):
        self.boxes = [list(BOX)]

    def detect_persons(self, frame, confidence, min_height):
        return [Detection(confidence=0.9, bbox=list(box)) for box in self.boxes]


class Faces:
    """The face models: every person shows `look`, or no face at all."""
    ready = False

    def __init__(self, look: FaceEvidence | None = None):
        self.look = look

    @property
    def active(self):
        return self.look is not None

    def analyze(self, frame, detections, threshold, keep_faces=False):
        return [self.look or FaceEvidence() for _ in detections]


def camera(env, faces=None):
    cam = CameraUnit(CameraConfig(id="garden", name="Garden", source="none"), env.settings, detector=People(),
                     faces=faces or Faces(), events=env.events, learning=env.learning)
    cam.brain = CameraBrain("garden", cam.name, FakeSpeaker(), FakeRecorder(), settings=env.settings, ai=FakeAI(),
                            notifier=FakeNotifier(), events=env.events)
    cam.brain.details = cam.details
    return cam


def look(cam, t):
    cam._detect(np.zeros((H, W, 3), np.uint8), t, cam.settings.get(), False)
    return cam._detections


def teach_spot(env, box=SPOT):
    env.learning.feedback(event(env, person(box)), "false_alarm")


# ---- remembering what was seen -----------------------------------------------------------------
def test_details_are_stored_and_returned(env):
    marker = last_id(env)
    cam = camera(env)
    look(cam, 1000)
    [detection] = events_after(marker, "DETECTION")
    [p] = detection["details"]["people"]
    assert detection["details"]["camera_id"] == "garden" and detection["feedback"] is None
    assert p == {"box": SPOT, "confidence": 0.9, "status": "unknown", "identity": None, "track_id": 1,
                 "face_visible": False, "match_score": None}

    insider = camera(env, Faces(FaceEvidence(True, True, "Alice", 0.8)))
    look(insider, 1000)
    [recognised] = events_after(marker, "INSIDER")
    assert recognised["details"]["insider"] == "Alice"
    assert recognised["details"]["people"][0]["identity"] == "Alice"

    simulated = Detection(confidence=0.99, bbox=[0, 0, 640, 720], simulated=True)
    assert event_details("garden", W, H, [simulated])["people"][0]["simulated"] is True
    assert event_details("garden", W, H, []) is None, "nobody in the picture: nothing to keep"


# ---- feedback ---------------------------------------------------------------------------------------
def test_false_alarm_can_be_set_changed_and_cleared(env):
    e = event(env)
    r = env.learning.feedback(e, "false_alarm")
    assert r == {"id": e, "feedback": "false_alarm",
                 "message": "Got it. Guardian will ignore that spot on Garden while nothing moves there."}
    [spot] = env.learning.overview()["spots"]
    assert spot["camera"] == "Garden" and spot["taught"] == 1 and spot["events"] == [e] and spot["box"] == SPOT
    assert stored(e)["feedback"] == "false_alarm"
    assert env.learning.feedback(e, "false_alarm")["message"] == "Nothing changed."

    r = env.learning.feedback(e, "real")  # changing the verdict takes back the old lesson first
    assert r["message"] == "Guardian took back the spot it learned from this. Thanks. Noted as a real detection."
    assert env.learning.overview()["spots"] == [] and stored(e)["feedback"] == "real"

    assert env.learning.feedback(e, None)["message"] == "Cleared."
    assert stored(e)["feedback"] is None
    env.learning.feedback(e, "false_alarm")
    assert env.learning.feedback(e, None)["message"] == "Guardian took back the spot it learned from this."
    assert env.learning.overview()["spots"] == []


def test_correct_removes_an_overlapping_spot_and_clearing_puts_it_back(env):
    teach_spot(env)
    e = event(env, person([SPOT[0] + 0.01, SPOT[1], SPOT[2] + 0.01, SPOT[3]]))
    r = env.learning.feedback(e, "real")
    assert r["message"] == "Thanks. Guardian stopped ignoring that spot on Garden, because someone really was there."
    assert env.learning.overview()["spots"] == []
    assert env.learning.feedback(e, None)["message"] == "That spot is ignored again."
    assert len(env.learning.overview()["spots"]) == 1


def test_spots_are_merged_averaged_and_taken_back(env):
    a, b, far = [0.40, 0.20, 0.56, 0.90], [0.42, 0.22, 0.58, 0.92], [0.05, 0.3, 0.15, 0.7]
    e1, e2, e3 = event(env, person(a)), event(env, person(b)), event(env, person(far), person(a, track_id=2))
    env.learning.feedback(e1, "false_alarm")
    env.learning.feedback(e2, "false_alarm")
    [spot] = env.learning.overview()["spots"]
    assert spot["taught"] == 2 and spot["events"] == [e1, e2] and spot["box"] == pytest.approx([0.41, 0.21, 0.57, 0.91])
    r = env.learning.feedback(e3, "false_alarm")
    assert r["message"] == "Got it. Guardian will ignore those 2 spots on Garden while nothing moves there."
    spots = {s["taught"]: s for s in env.learning.overview()["spots"]}
    assert set(spots) == {1, 3} and spots[1]["box"] == far
    env.learning.feedback(e1, None)
    spots = {s["taught"]: s for s in env.learning.overview()["spots"]}
    assert set(spots) == {1, 2} and spots[2]["events"] == [e2, e3]


def test_feedback_rules(env):
    detection = event(env)
    with pytest.raises(LearningError):
        env.learning.feedback(detection, "wrong_person")
    with pytest.raises(LearningError):
        env.learning.feedback(event(env, person(status="known", identity="Alice"), event_type="INSIDER",
                                    insider="Alice"), "false_alarm")
    with pytest.raises(LearningError):
        env.learning.feedback(env.events.log("DETECTION", "an older event, without details"), "false_alarm")
    with pytest.raises(KeyError):
        env.learning.feedback(10 ** 9, "real")

    test = event(env, person(simulated=True))
    assert "test intrusion" in env.learning.feedback(test, "false_alarm")["message"]
    pending = event(env, person(status="pending"))
    assert "Nobody unrecognised" in env.learning.feedback(pending, "false_alarm")["message"]
    assert env.learning.overview()["spots"] == [], "nothing learned from tests or people still being identified"

    env.settings.update({"learning": {"learn_from_feedback": False}})
    r = env.learning.feedback(detection, "false_alarm")
    assert "Learning from feedback is off" in r["message"] and stored(detection)["feedback"] == "false_alarm"
    assert env.learning.overview()["spots"] == [], "recorded, but teaches nothing"


# ---- ignored spots ----------------------------------------------------------------------------------
def test_a_still_box_on_a_spot_is_ignored(env):
    teach_spot(env)
    marker = last_id(env)
    cam = camera(env)
    for t in (1000, 1000.4, 1000.8):
        [d] = look(cam, t)
        assert d.ignored and d.status == "unknown"
    assert cam.brain.persons == 0 and cam.brain.threat_level == 0
    assert events_after(marker) == [], "no incident while it stays still"
    assert env.learning.overview()["spots"][0]["last_matched"] == 1000.8

    frame = np.zeros((H, W, 3), np.uint8)
    draw_overlay(frame, [d], "Garden", True)
    assert tuple(frame[400, BOX[0]]) == DIM, "drawn thin and dim"
    assert tuple(frame[400, BOX[0] + 3]) == (0, 0, 0)


def test_moving_or_face_showing_people_are_never_ignored(env):
    teach_spot(env)
    marker = last_id(env)

    walker = camera(env)  # walks into the spot
    walker.detector.boxes = [[BOX[0] - 100, BOX[1], BOX[2] - 100, BOX[3]]]
    look(walker, 1000)
    walker.detector.boxes = [list(BOX)]
    assert not look(walker, 1000.4)[0].ignored
    assert len(events_after(marker, "DETECTION")) == 1

    stayer = camera(env)  # ignored while still, counts again the moment it moves
    assert look(stayer, 2000)[0].ignored
    stayer.detector.boxes = [[BOX[0] + 60, BOX[1], BOX[2] + 60, BOX[3]]]  # 60 px is 4.7% of the width
    assert not look(stayer, 2000.4)[0].ignored
    stayer.detector.boxes = [list(BOX)]
    assert not look(stayer, 2000.8)[0].ignored, "the track has moved, back on the spot or not"

    resized = camera(env)  # stood up or sat down
    look(resized, 3000)
    resized.detector.boxes = [[BOX[0], BOX[1] + 150, BOX[2], BOX[3]]]
    assert not look(resized, 3000.4)[0].ignored

    face = camera(env, Faces(FaceEvidence(True, True, None, 0.1)))  # faces the camera on the spot
    assert not any(d.ignored for t in (4000, 4000.4) for d in look(face, t))

    insider = camera(env, Faces(FaceEvidence(True, True, "Alice", 0.8)))
    [d] = look(insider, 5000)
    assert d.status == "known" and not d.ignored


def test_insiders_and_test_intrusions_are_never_ignored(env):
    teach_spot(env)
    tracks = {1: Track(1, list(BOX), 1000, 1000, first_bbox=list(BOX))}  # still, no face seen
    insider = Detection(confidence=0.9, bbox=list(BOX), track_id=1, status="known", identity="Alice")
    test = Detection(confidence=0.99, bbox=list(BOX), track_id=1, simulated=True)
    stranger = Detection(confidence=0.9, bbox=list(BOX), track_id=1)
    env.learning.judge("garden", "Garden", np.zeros((H, W, 3), np.uint8), [insider, test, stranger], tracks, 1000)
    assert (insider.ignored, test.ignored, stranger.ignored) == (False, False, True)
    elsewhere = stranger.model_copy(update={"ignored": False})
    env.learning.judge("porch", "Porch", np.zeros((H, W, 3), np.uint8), [elsewhere], tracks, 2000)
    assert not elsewhere.ignored and env.learning.overview()["spots"][0]["last_matched"] == 1000, \
        "spots belong to one camera"


# ---- suggestions -----------------------------------------------------------------------------------
def stay(cam, start, seconds, step=2.0):
    t = start
    while t <= start + seconds:
        look(cam, t)
        t += step
    return t


def test_a_still_thing_is_suggested_after_ten_minutes_once_per_place(env):
    env.settings.update({"armed": False})  # nothing else to log meanwhile
    marker = last_id(env)
    start = time.time()
    cam = camera(env)
    t = stay(cam, start, SUGGEST_AFTER - 2)
    assert env.learning.overview()["suggestions"] == []
    t = stay(cam, t, 10)
    env.learning.process_pending()
    [suggestion] = env.learning.overview()["suggestions"]
    assert suggestion["camera"] == "Garden" and suggestion["box"] == SPOT
    [system] = events_after(marker, "SYSTEM")
    assert system["description"] == ("Something on Garden has not moved for 10 minutes and shows no face. "
                                     "If it is an object, confirm it on the Learning page.")
    assert system["snapshot"] and suggestion["event"]["id"] == system["id"]
    assert not look(cam, t)[0].ignored, "only suggested: nothing is ignored until the owner confirms"

    stay(cam, t, 60)
    env.learning.process_pending()
    assert len(env.learning.overview()["suggestions"]) == 1, "at most one suggestion per place"

    r = env.learning.answer(suggestion["id"], accept=False)
    assert r["message"] == "Dismissed. Guardian won't suggest that place on Garden again for a day."
    later = camera(env)
    stay(later, start + 3600, SUGGEST_AFTER + 10)
    assert env.learning.overview()["suggestions"] == [], "dismissed for a day"
    next_day = camera(env)
    stay(next_day, start + DISMISS_FOR + 60, SUGGEST_AFTER + 10)
    [again] = env.learning.overview()["suggestions"]

    r = env.learning.answer(again["id"], accept=True)
    assert r["message"] == "Got it. Guardian will ignore that spot on Garden while nothing moves there."
    assert env.learning.overview()["suggestions"] == [] and env.learning.overview()["spots"][0]["taught"] == 1
    assert look(next_day, start + DISMISS_FOR + 2000)[0].ignored
    with pytest.raises(KeyError):
        env.learning.answer(again["id"], accept=True)


def test_a_removed_spot_is_not_suggested_again_straight_away(env):
    env.settings.update({"armed": False})
    teach_spot(env)
    env.learning.delete_spot(env.learning.overview()["spots"][0]["id"])
    cam = camera(env)
    start = time.time()
    assert not look(cam, start)[0].ignored
    stay(cam, start, SUGGEST_AFTER + 10)
    assert env.learning.overview()["suggestions"] == []
    stay(camera(env), start + DISMISS_FOR + 60, SUGGEST_AFTER + 10)
    assert len(env.learning.overview()["suggestions"]) == 1, "only for a day"


def test_moving_things_and_faces_are_not_suggested(env):
    env.settings.update({"armed": False})
    start = time.time()
    cam = camera(env)
    t, x = start, 0
    while t <= start + SUGGEST_AFTER + 10:
        cam.detector.boxes = [[BOX[0] + x, BOX[1], BOX[2] + x, BOX[3]]]
        look(cam, t)
        t, x = t + 2, (x + 8) % 160  # paces up and down
    face = camera(env, Faces(FaceEvidence(True, True, None, 0.1)))
    stay(face, start, SUGGEST_AFTER + 10)
    assert env.learning.overview()["suggestions"] == []


# ---- faces that improve themselves ---------------------------------------------------------------------
def enrol(env, name="Alice", feature=ALICE):
    folder = env.faces.faces_dir / name
    folder.mkdir()
    cv2.imwrite(str(folder / "owner.jpg"), np.full((120, 100, 3), 90, np.uint8))
    np.save(folder / "owner.npy", feature)
    env.faces._gallery[name] = [("owner.jpg", feature)]


def face(name="Alice", score=0.7, feature=NEW_LOOK, quality=True):
    crop = np.full((120, 100, 3), 160, np.uint8)
    return FaceEvidence(True, quality, name, score, feature=feature if quality else None,
                        crop=crop if quality else None, quality=0.8)


class Sighting:
    """One insider followed by a camera's tracker; each look() is a detection run."""

    def __init__(self, env, start=10_000.0):
        self.env, self.t, self.tracker = env, start, Tracker()

    def look(self, ev: FaceEvidence, step=0.4):
        self.t += step
        detections = [Detection(confidence=0.9, bbox=list(BOX))]
        self.tracker.update(detections, [ev], self.t, T, 2.0, True)
        self.env.learning.offer_faces("garden", "Garden", detections, [ev], self.tracker.tracks, T, self.t)
        self.env.learning.process_pending()
        return detections[0]

    def stay(self, seconds: float, ev: FaceEvidence):
        """Stays in view, looked at every 2 s (the tracker forgets people unseen for 2.5 s)."""
        for _ in range(int(seconds // 2)):
            self.look(ev, step=2.0)


def learned(env, name="Alice"):
    return sorted(p.name for p in (env.faces.faces_dir / name).glob(f"{LEARNED_PREFIX}*.jpg"))


def test_a_new_look_is_learned_after_several_agreeing_frames(env):
    enrol(env)
    marker = last_id(env)
    s = Sighting(env)
    assert s.look(face()).status == "known"
    s.look(face())
    assert learned(env) == [], "two looks are not enough"
    s.look(face())
    [file] = learned(env)
    assert env.faces.similarity("Alice", NEW_LOOK) == pytest.approx(1.0), "used for recognition at once"
    [event] = events_after(marker, "LEARNED")
    assert event["description"] == "Learned a new photo of Alice from Garden" and event["snapshot"]
    [photo] = env.faces.learned_photos()
    assert photo["camera_id"] == "garden" and photo["track"] == 1 and photo["learned"] == pytest.approx(s.t)
    insider = env.faces.list_insiders()[0]
    assert {p["file"]: p["learned"] for p in insider["photos"]} == {"owner.jpg": False, file: True}


@pytest.mark.parametrize("why, looks", [
    ("too close to the threshold", [face(score=T + 0.06)] * 5),
    ("failed the quality gate", [face(quality=False)] * 5),
    ("nothing new about it", [face(feature=SAME_LOOK)] * 5),
    ("contested: a vote for someone else", [face(), face(), face("Bob", 0.4), face(), face()]),
    ("the face itself did not match: identity only from the tracker's memory",
     [face(feature=SAME_LOOK)] * 3 + [face(None, 0.3)]),
])
def test_faces_are_not_learned_when_a_gate_fails(env, why, looks):
    enrol(env)
    s = Sighting(env)
    for ev in looks:
        s.look(ev)
    assert learned(env) == [], why


def test_face_learning_is_rate_limited_capped_and_can_be_turned_off(env):
    enrol(env)
    s = Sighting(env)
    for _ in range(4):
        s.look(face())
    assert len(learned(env)) == 1
    another = face(feature=unit([0.7, 0, 0.7, 0]))
    s.stay(LEARN_EVERY - 10, another)
    assert len(learned(env)) == 1, f"at most one per {LEARN_EVERY / 60:.0f} minutes"
    s.stay(20, another)
    assert len(learned(env)) == 2

    for i in range(MAX_LEARNED):
        env.faces.add_learned("Alice", np.zeros((80, 80, 3), np.uint8), unit([0.5, 0, 0, 0.1 * (i + 1)]),
                              {"learned": 100.0 + i}, MAX_LEARNED)
    assert len(learned(env)) == MAX_LEARNED
    oldest = sorted(env.faces.learned_photos(), key=lambda p: p["learned"])[0]["file"]
    s.stay(LEARN_EVERY + 10, face(feature=unit([0.6, 0, 0, -0.8])))
    files = learned(env)
    assert len(files) == MAX_LEARNED and oldest not in files, "the oldest learned photo made way"
    assert (env.faces.faces_dir / "Alice" / "owner.jpg").exists(), "never a photo the owner added"

    env.settings.update({"learning": {"improve_faces": False}})
    s.stay(LEARN_EVERY + 10, face(feature=unit([0, 0, 0.6, 0.8])))
    assert len(learned(env)) == MAX_LEARNED


def test_not_this_person_sets_learned_photos_aside_and_clearing_brings_them_back(env):
    enrol(env)
    s = Sighting(env, start=time.time())
    for _ in range(3):
        d = s.look(face())
    [file] = learned(env)
    e = event(env, person(status="known", identity="Alice", track_id=d.track_id), event_type="INSIDER", insider="Alice")
    r = env.learning.feedback(e, "wrong_person")
    assert r["message"] == "Got it. Removed 1 photo of Alice that Guardian had learned from this sighting."
    assert learned(env) == [] and env.faces.similarity("Alice", NEW_LOOK) == pytest.approx(0.8)
    s.stay(LEARN_EVERY + 10, face(feature=unit([0.7, 0, 0.7, 0])))
    assert learned(env) == [], "nothing more is learned from that sighting"

    assert env.learning.feedback(e, None)["message"] == "That photo of Alice is back."
    assert learned(env) == [file] and env.faces.similarity("Alice", NEW_LOOK) == pytest.approx(1.0)

    other = event(env, person(status="known", identity="Alice", track_id=99), event_type="INSIDER", insider="Alice")
    with SessionLocal() as db:  # a sighting a day earlier
        db.get(SecurityEvent, other).timestamp -= timedelta(days=1)
        db.commit()
    assert "had not learned any photos of Alice" in env.learning.feedback(other, "wrong_person")["message"]
    assert learned(env) == [file]


# ---- persistence ------------------------------------------------------------------------------------
def test_what_was_learned_survives_a_restart(env):
    enrol(env)
    teach_spot(env)
    s = Sighting(env, start=time.time())
    for _ in range(3):
        s.look(face())
    assert len(learned(env)) == 1
    env.learning._suggestions.append({"id": "s1", "camera_id": "garden", "box": [0.1, 0.1, 0.2, 0.5],
                                      "created": 1.0, "event_id": None, "picture": None})
    env.learning._save()

    again = LearningService(env.tmp / "learning", settings=env.settings, faces=env.faces, events=env.events)
    overview = again.overview()
    assert [sp["box"] for sp in overview["spots"]] == [SPOT] and [sg["id"] for sg in overview["suggestions"]] == ["s1"]
    assert overview["faces"][0]["learned"] == 1
    env.learning = again
    s.stay(60, face(feature=unit([0.7, 0, 0.7, 0])))
    assert len(learned(env)) == 1, "the limit of one per 10 minutes holds across restarts"
    assert (env.tmp / "learning" / "spots.json").exists() and not (env.tmp / "learning" / "spots.tmp").exists()


def test_an_older_event_log_gets_the_new_columns(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE security_events (id INTEGER PRIMARY KEY, timestamp DATETIME, "
                          "event_type VARCHAR, description VARCHAR, severity VARCHAR)"))
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    assert {"details", "feedback"} <= {c["name"] for c in inspect(engine).get_columns("security_events")}


# ---- API ---------------------------------------------------------------------------------------------
@pytest.fixture
def client(env, monkeypatch):
    monkeypatch.setattr(api_module, "learning_service", env.learning)
    monkeypatch.setattr(api_module, "settings_service", env.settings)
    app = FastAPI()
    app.include_router(api_module.router)
    return TestClient(app)


def test_api(client, env):
    e = event(env)
    r = client.post(f"/api/events/{e}/feedback", json={"verdict": "false_alarm"})
    assert r.status_code == 200 and r.json()["message"].startswith("Got it. Guardian will ignore that spot on Garden")
    listed = next(i for i in client.get("/api/events", params={"camera": "Garden"}).json()["items"] if i["id"] == e)
    assert listed["feedback"] == "false_alarm" and listed["details"]["people"][0]["box"] == SPOT
    assert client.post(f"/api/events/{e}/feedback", json={"verdict": "maybe"}).status_code == 422
    assert client.post(f"/api/events/{e}/feedback", json={"verdict": "wrong_person"}).status_code == 409
    assert client.post("/api/events/999999999/feedback", json={"verdict": None}).status_code == 404

    overview = client.get("/api/learning").json()
    assert overview["settings"] == {"learn_from_feedback": True, "improve_faces": True, "unusual_activity": "alert"}
    assert overview["rules"]["still_minutes"] == 10 and overview["feedback"]["days"] == 30
    garden = next(c for c in overview["feedback"]["cameras"] if c["camera"] == "Garden")
    assert garden["false_alarm"] >= 1
    [spot] = overview["spots"]
    assert client.delete(f"/api/learning/spots/{spot['id']}").json() == {"ok": True}
    assert client.delete(f"/api/learning/spots/{spot['id']}").status_code == 404

    env.learning._suggestions.append({"id": "s1", "camera_id": "garden", "box": SPOT, "created": 1.0,
                                      "event_id": None, "picture": None})
    assert client.post("/api/learning/suggestions/s1", json={"accept": True}).json()["spot"]
    assert client.post("/api/learning/suggestions/s1", json={"accept": True}).status_code == 404
    assert client.delete("/api/learning").status_code == 422, "one camera at a time"
    assert client.delete("/api/learning", params={"camera": "garden"}).json() == {"spots": 1, "suggestions": 0,
                                                                                   "photos": 0}

    teach_spot(env)
    assert client.delete("/api/cameras/garden").status_code == 200
    assert client.get("/api/learning").json()["spots"] == [], "a removed camera's spots go with it"
