import cv2
import numpy as np
import pytest

from models.domain import Detection
from services.face_service import FaceError, FaceService, safe_name


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def face(size=80, score=0.95, nose_x=0.5, x=100.0, y=100.0, height=None):
    """A YuNet-style row: box, five landmarks, score. nose_x moves the nose to simulate turning."""
    w, h = size, height or size
    re, le = (x + w * 0.3, y + h * 0.4), (x + w * 0.7, y + h * 0.4)
    nose = (x + w * nose_x, y + h * 0.6)
    mouth_r, mouth_l = (x + w * 0.35, y + h * 0.8), (x + w * 0.65, y + h * 0.8)
    return np.array([x, y, w, h, *re, *le, *nose, *mouth_r, *mouth_l, score], np.float32)


@pytest.fixture
def faces(tmp_path):
    svc = FaceService(tmp_path / "faces", tmp_path / "models")
    (tmp_path / "faces").mkdir()
    svc._gallery = {
        "Sam": [("a.jpg", unit([1, 0, 0, 0])), ("b.jpg", unit([0.9, 0.1, 0, 0]))],
        "Ana": [("c.jpg", unit([0, 1, 0, 0]))],
    }
    return svc


def test_match_picks_closest_insider(faces):
    name, score, second = faces._match(unit([0.95, 0.05, 0.2, 0]), 0.36)
    assert name == "Sam" and score > 0.9 and second < 0.36


def test_stranger_is_not_matched(faces):
    name, score, _ = faces._match(unit([0, 0, 1, 0]), 0.36)
    assert name is None and score < 0.36


def test_ambiguous_match_is_refused(faces):
    # Exactly halfway between Sam's nearest photo and Ana: better to say "unknown" than to guess.
    sam_b, ana = faces._gallery["Sam"][1][1], faces._gallery["Ana"][0][1]
    name, best, second = faces._match(unit(sam_b + ana), 0.36)
    assert name is None and best >= 0.36 and best - second < 0.06


def test_yaw_estimate():
    assert FaceService._yaw(face(nose_x=0.5)) < 0.05
    assert FaceService._yaw(face(nose_x=0.85)) > 0.8


@pytest.mark.parametrize("row, sharpness, ok, reason", [
    (face(), 120, True, None),
    (face(score=0.75), 120, False, "not clearly visible"),
    (face(size=20), 120, False, "too small"),
    (face(nose_x=0.9), 120, False, "turned away"),
    (face(), 5, False, "blurry"),
])
def test_quality_gate(faces, row, sharpness, ok, reason):
    good, why = faces._quality(row, sharpness)
    assert good is ok and (reason is None or reason in why)


def test_small_face_from_enlarged_crop_is_judged_at_camera_size(faces):
    # 60 px after a 3x enlargement is really a 20 px face.
    assert faces._quality(face(size=60), 120, px_scale=3.0) == (False, "face too small")


def test_names_are_sanitised():
    assert safe_name("  Sam  O'Neil ") == "Sam ONeil"
    assert safe_name("../../etc") == "etc"
    with pytest.raises(FaceError):
        safe_name("../..")


def test_photo_paths_cannot_escape(faces, tmp_path):
    (tmp_path / "faces" / "Sam").mkdir()
    (tmp_path / "faces" / "Sam" / "a.jpg").write_bytes(b"x")
    assert faces.photo_path("Sam", "a.jpg").name == "a.jpg"
    for name, file in (("Sam", "../../models/x.onnx"), ("..", "a.jpg"), ("Sam", "missing.jpg")):
        with pytest.raises((FileNotFoundError, FaceError)):
            faces.photo_path(name, file)


def test_analyze_is_a_no_op_without_insiders(tmp_path):
    from models.domain import Detection
    svc = FaceService(tmp_path / "f", tmp_path / "m")
    ev = svc.analyze(np.zeros((100, 100, 3), np.uint8), [Detection(confidence=0.9, bbox=[0, 0, 50, 90])], 0.36)
    assert len(ev) == 1 and ev[0].face_visible is False


# ---- which face belongs to which person ----------------------------------------------
# Test frames are black with a coloured square for each visible face. A stand-in face detector
# finds the squares in whatever it is shown, the whole frame or an enlarged crop of it.
INSIDER, STRANGER = (0, 0, 255), (0, 255, 0)
FEATURES = {INSIDER: unit([1, 0, 0, 0]), STRANGER: unit([0, 0, 1, 0])}


def find_squares(img):
    """Like YuNet, misses faces under 24 px. A square cut off by the edge of a crop is still found."""
    found = []
    for colour in FEATURES:
        ys, xs = np.nonzero(np.all(np.abs(img.astype(int) - colour) < 60, axis=2))
        if len(xs) and xs.max() - xs.min() + 1 >= 24:
            found.append(face(size=float(xs.max() - xs.min() + 1), x=float(xs.min()), y=float(ys.min()),
                              height=float(ys.max() - ys.min() + 1)))
    return found


def square_features(img, row):
    pixel = img[int(row[1] + row[3] / 2), int(row[0] + row[2] / 2)].astype(int)
    colour = min(FEATURES, key=lambda c: np.abs(pixel - c).sum())
    return FEATURES[colour], 120.0


@pytest.fixture
def scene(faces, monkeypatch):
    faces._recognizer = object()  # models loaded
    faces._gallery = {"Carlo": [("a.jpg", FEATURES[INSIDER])]}
    monkeypatch.setattr(faces, "_detect", find_squares)
    monkeypatch.setattr(faces, "_embed", square_features)
    return faces


def frame_with(*squares):
    frame = np.zeros((720, 1280, 3), np.uint8)
    for colour, (x1, y1, x2, y2) in squares:
        cv2.rectangle(frame, (x1, y1), (x2, y2), colour, -1)
    return frame


def people(*boxes):
    return [Detection(confidence=0.9, bbox=list(box)) for box in boxes]


# From a real clip: a stranger leaning forward with his arm stretched out, so his box reaches far
# across the insider standing next to him.
STRANGER_BOX, INSIDER_BOX = [117, 199, 1111, 712], [748, 42, 1132, 714]
INSIDER_FACE, STRANGER_FACE = (920, 100, 1050, 270), (555, 262, 655, 424)


def test_overlapping_people_each_get_their_own_face(scene):
    frame = frame_with((INSIDER, INSIDER_FACE), (STRANGER, STRANGER_FACE))
    stranger, insider = scene.analyze(frame, people(STRANGER_BOX, INSIDER_BOX), 0.36)
    assert insider.name == "Carlo"
    assert stranger.face_visible and stranger.name is None


def test_a_neighbours_face_is_not_given_to_someone_turned_away(scene):
    # The second look at the stranger's head area also shows part of the insider's face.
    frame = frame_with((INSIDER, INSIDER_FACE))
    stranger, insider = scene.analyze(frame, people(STRANGER_BOX, INSIDER_BOX), 0.36)
    assert insider.name == "Carlo"
    assert not stranger.face_visible and stranger.name is None


def test_a_small_face_seen_in_two_crops_goes_to_one_person(scene):
    # Two people far away; only the insider's face shows, too small for the whole-frame look.
    frame = frame_with((INSIDER, (640, 300, 659, 319)))
    stranger, insider = scene.analyze(frame, people([500, 300, 660, 700], [580, 280, 700, 700]), 0.36)
    assert insider.face_visible and insider.name == "Carlo"
    assert not stranger.face_visible
