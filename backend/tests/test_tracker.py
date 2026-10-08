from models.domain import Detection
from services.tracker import FaceEvidence, Tracker

T = 0.36


def det(x=0.0):
    return Detection(class_name="person", confidence=0.9, bbox=[x, 0, x + 100, 250])


def run(tracker, now, evidence, detections=None, identify=2.0, active=True):
    detections = detections or [det()]
    tracker.update(detections, evidence, now, T, identify, active)
    return detections


def test_strong_match_identifies_immediately():
    tr = Tracker()
    d = run(tr, 0, [FaceEvidence(True, True, "Sam", 0.8)])[0]
    assert d.status == "known" and d.identity == "Sam"


def test_weak_matches_need_two_looks():
    tr = Tracker()
    d = run(tr, 0, [FaceEvidence(True, True, "Sam", 0.40)])[0]
    assert d.status == "pending"
    d = run(tr, 0.4, [FaceEvidence(True, True, "Sam", 0.42)])[0]
    assert d.status == "known" and d.identity == "Sam"


def test_identity_sticks_when_face_turns_away():
    tr = Tracker()
    run(tr, 0, [FaceEvidence(True, True, "Sam", 0.8)])
    for t in (0.4, 0.8, 2.0, 3.5, 5.0):  # face not visible, but the person stays in view
        d = run(tr, t, [FaceEvidence()])[0]
    assert d.status == "known"


def test_clear_stranger_face_is_flagged_without_waiting():
    tr = Tracker()
    run(tr, 0, [FaceEvidence(True, True, None, 0.1)])
    d = run(tr, 0.4, [FaceEvidence(True, True, None, 0.12)])[0]
    assert d.status == "unknown"


def test_unidentified_person_becomes_unknown_after_window():
    tr = Tracker()
    assert run(tr, 0, [FaceEvidence()])[0].status == "pending"
    assert run(tr, 1.9, [FaceEvidence()])[0].status == "pending"
    assert run(tr, 2.0, [FaceEvidence()])[0].status == "unknown"


def test_poor_quality_faces_do_not_count_against_anyone():
    tr = Tracker()
    for t in (0, 0.4, 0.8):
        d = run(tr, t, [FaceEvidence(True, False, None, 0.1)])[0]
    assert d.status == "pending"


def test_without_recognition_everyone_is_unknown():
    tr = Tracker()
    assert run(tr, 0, [FaceEvidence()], active=False)[0].status == "unknown"


def test_tracks_follow_moving_people_and_expire():
    tr = Tracker()
    first = run(tr, 0, [FaceEvidence(True, True, "Sam", 0.8)], [det(0)])[0]
    moved = run(tr, 0.4, [FaceEvidence()], [det(60)])[0]
    assert moved.track_id == first.track_id and moved.identity == "Sam"
    later = run(tr, 10, [FaceEvidence()], [det(60)])[0]
    assert later.track_id != first.track_id, "a person gone for seconds is a new track"


def test_two_people_keep_separate_identities():
    tr = Tracker()
    a, b = run(tr, 0, [FaceEvidence(True, True, "Sam", 0.8), FaceEvidence(True, True, None, 0.1)],
               [det(0), det(400)])
    a, b = run(tr, 0.4, [FaceEvidence(), FaceEvidence(True, True, None, 0.1)], [det(5), det(405)])
    assert (a.status, a.identity) == ("known", "Sam")
    assert b.status == "unknown"


def test_simulated_person_is_always_a_stranger():
    tr = Tracker()
    d = Detection(class_name="person", confidence=0.99, bbox=[0, 0, 100, 250], simulated=True)
    tr.update([d], [FaceEvidence()], 0, T, 5.0, True)
    assert d.status == "unknown"
