from models.domain import Detection
from services.object_watch import UnattendedWatcher, describe

W = 1000


def bag(x=500, y=600, kind="backpack"):
    return Detection(class_name=kind, confidence=0.6, bbox=[x, y, x + 60, y + 80])


def person(x, y=300):
    return Detection(confidence=0.9, bbox=[x, y, x + 100, y + 380])


def run(w, frames, step=5.0, start=0.0, after=60):
    fired = []
    for i, (things, people) in enumerate(frames):
        fired += w.update(things, people, W, start + i * step, after)
    return fired


def test_reports_a_bag_left_alone_once():
    w = UnattendedWatcher()
    # The owner stands next to it for 20 s, then leaves; it stays for over a minute.
    frames = [([bag()], [person(470)])] * 4 + [([bag()], [])] * 20
    fired = run(w, frames)
    assert len(fired) == 1 and fired[0].kind == "backpack"
    assert describe(fired[0], 4 * 5 + 19 * 5) == "Backpack left unattended for 2 min"
    assert w.flagged(115.0)


def test_not_while_someone_stays_with_it():
    w = UnattendedWatcher()
    assert run(w, [([bag(kind="suitcase")], [person(470)])] * 40) == []


def test_moving_restarts_the_clock_and_forgets_after_gone():
    w = UnattendedWatcher()
    frames = [([bag(500 + 80 * (i % 2))], []) for i in range(30)]  # keeps being moved about
    assert run(w, frames) == []
    w2 = UnattendedWatcher()
    assert len(run(w2, [([bag()], [])] * 20)) == 1
    run(w2, [([], [])] * 8, start=200)  # picked up: forgotten after 30 s unseen
    assert not w2.objects
    assert len(run(w2, [([bag()], [])] * 20, start=300)) == 1, "put down again later counts again"


def test_briefly_hidden_object_keeps_its_clock():
    w = UnattendedWatcher()
    frames = [([bag()], [])] * 8 + [([], [person(100)])] * 3 + [([bag()], [])] * 6
    assert len(run(w, frames)) == 1


# ---- in the camera pipeline ----------------------------------------------------------------------
class BagDetector:
    error = None

    def __init__(self):
        self.asked = []

    def detect(self, frame, confidence, min_height, objects=False):
        self.asked.append(objects)
        return [], ([bag()] if objects else [])


def test_camera_logs_and_alerts_unattended_bag(tmp_path):
    import time

    import numpy as np
    from test_brain import FakeEvents, FakeNotifier

    from services.camera_service import CameraUnit
    from services.settings_service import CameraConfig, SettingsService

    settings = SettingsService(tmp_path / "settings.json")
    settings.update({"armed": True, "detection": {"face_recognition": False, "unattended_objects": "alert",
                                                  "unattended_minutes": 1}})
    events, notifier = FakeEvents(), FakeNotifier()
    unit = CameraUnit(CameraConfig(id="hall", name="Hall", source="none"), settings, detector=BagDetector(),
                      events=events, notifier=notifier)
    unit.brain.events = FakeEvents()
    frame = np.zeros((720, 1280, 3), np.uint8)
    now = time.time()
    for i in range(20):
        unit._detect(frame, now + i * 5, settings.get(), False)
    [(kind, text)] = [e for e in events.descriptions if e[0] == "UNATTENDED"]
    assert text == "Backpack left unattended for 1 min"
    assert [p for k, p in events.pictures if k == "UNATTENDED"][0][:2] == b"\xff\xd8", "with a picture"
    for _ in range(50):
        if notifier.alerts:
            break
        time.sleep(0.02)
    assert notifier.alerts == ["Unattended object: Hall"]
    settings.update({"armed": False})
    unit._detect(frame, now + 200, settings.get(), False)
    assert unit.detector.asked[-1] is False and not unit.objects.objects, "nothing watched while disarmed"
