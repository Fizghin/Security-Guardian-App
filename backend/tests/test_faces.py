import numpy as np
import pytest

from services.face_service import FaceError, FaceService, safe_name


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def face(size=80, score=0.95, nose_x=0.5):
    """A YuNet-style row: box, five landmarks, score. nose_x moves the nose to simulate turning."""
    x, y = 100.0, 100.0
    re, le = (x + size * 0.3, y + size * 0.4), (x + size * 0.7, y + size * 0.4)
    nose = (x + size * nose_x, y + size * 0.6)
    mouth_r, mouth_l = (x + size * 0.35, y + size * 0.8), (x + size * 0.65, y + size * 0.8)
    return np.array([x, y, size, size, *re, *le, *nose, *mouth_r, *mouth_l, score], np.float32)


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
