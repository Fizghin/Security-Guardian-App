import math
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from models.domain import Detection
from services import bag_watch
from services.bag_watch import BagWatch
from services.detection_service import BAG_CLASSES, DetectionService, detection_service
from services.fall_watch import FallWatch, lying
from services.sentinel_service import ASK_TEXT, Sentinel, before_after, duration
from services.settings_service import CameraConfig, SettingsError, SettingsService
from services.spot_watch import SpotWatch
from test_brain import FakeEvents, FakeNotifier, FakeRecorder, FakeSpeaker

W, H = 1280, 720
LIMIT = 120  # the default: 2 minutes


def bag(box=(600, 500, 660, 580)) -> Detection:
    return Detection(class_name="suitcase", confidence=0.6, bbox=list(box))


NEAR = [680, 300, 780, 600]   # 20 px to the bag's right: within one bag width (60 px)
AWAY = [100, 100, 200, 400]
OVER = [590, 300, 700, 600]   # standing over the bag


def run(watch: BagWatch, start: float, end: float, bags=(), people=(), step=2.0):
    out, t = [], start
    while t < end:
        out += watch.update([bag(b) for b in bags], [list(p) for p in people], t, W, H, LIMIT)
        t += step
    return out, t


def watching(t=20.0) -> BagWatch:
    """A watch that has been running for a while with no bags, so new bags aren't 'already there'."""
    watch = BagWatch()
    run(watch, 0, t)
    return watch


BAG = (600, 500, 660, 580)


# ---- unattended bags -------------------------------------------------------------------------------
def test_a_bag_is_left_after_the_set_minutes_with_nobody_near():
    watch = watching()
    out, t = run(watch, 20, 24, bags=[BAG], people=[NEAR])  # put down by someone who then walks away
    out, t = run(watch, t, 20 + LIMIT - 1, bags=[BAG], people=[AWAY])
    assert out == [], "not before the set time"
    out, t = run(watch, t, t + 4, bags=[BAG], people=[AWAY])
    [left] = out
    assert left.kind == "left" and 110 <= left.seconds <= 120
    assert duration(left.seconds) == "2 min"
    assert watch.left == [left.bag]
    assert run(watch, t, t + 60, bags=[BAG])[0] == [], "logged once"


def test_the_owner_standing_nearby_keeps_a_bag_attended():
    watch = watching()
    out, t = run(watch, 20, 20 + 5 * 60, bags=[BAG], people=[NEAR])
    assert out == []
    out, t = run(watch, t, t + 28, bags=[BAG], people=[AWAY])
    assert out == [], "nobody near it for less than 30 s"
    out, t = run(watch, t, t + 4, bags=[BAG])
    [left] = out
    assert left.kind == "left" and 28 <= left.seconds <= 34 and duration(left.seconds).endswith(" s")


def test_only_people_close_to_a_bag_count():
    assert bag_watch._near([600, 500, 660, 580], [680, 300, 780, 600])
    assert bag_watch._near([600, 500, 660, 580], OVER)
    assert not bag_watch._near([600, 500, 660, 580], [730, 300, 830, 600]), "70 px away: more than a bag width"


def test_a_moving_bag_is_never_left():
    watch = watching()
    out, t = [], 20.0
    for i in range(200):
        x = 100 + (i * 20) % 1000  # carried about slowly
        out += watch.update([bag((x, 500, x + 60, 580))], [], t, W, H, LIMIT)
        t += 2
    assert out == []


def test_small_jitter_still_counts_as_still():
    watch = watching()
    out, t = [], 20.0
    for i in range(70):
        dx = (i % 3) * 8  # detector boxes wobble by a few pixels
        out += watch.update([bag((600 + dx, 500, 660 + dx, 580))], [], t, W, H, LIMIT)
        t += 2
    assert [h.kind for h in out] == ["left"]


def test_a_left_bag_is_picked_up():
    watch = watching()
    run(watch, 20, 20 + LIMIT + 2, bags=[BAG])
    assert len(watch.left) == 1
    out, t = run(watch, 150, 170, people=[OVER])
    assert out == [], "someone standing in front of it may just hide it"
    out, t = run(watch, t, t + 8)
    assert out == []
    out, t = run(watch, t, t + 4)
    assert [h.kind for h in out] == ["picked_up"] and watch.bags == {}


def test_a_left_bag_that_is_moved_was_picked_up():
    watch = watching()
    run(watch, 20, 20 + LIMIT + 2, bags=[BAG])
    out, _ = run(watch, 150, 152, bags=[(600, 525, 660, 605)], people=[NEAR])  # 25 px: over 3% of the height
    assert [h.kind for h in out] == ["picked_up"] and watch.left == []


def test_bags_already_there_are_part_of_the_scene():
    watch = BagWatch()
    out, t = run(watch, 0, 30 * 60, bags=[BAG])
    assert out == [] and all(b.scene for b in watch.bags.values())
    run(watch, t, t + 120)  # the detector loses it for two minutes
    assert watch.bags == {}
    out, _ = run(watch, t + 120, t + 120 + 30 * 60, bags=[BAG])
    assert out == [], "found again in the same place: still part of the scene"


def test_a_bag_still_for_over_10_minutes_before_it_was_left_is_part_of_the_scene():
    watch = watching()
    out, t = run(watch, 20, 20 + 11 * 60, bags=[BAG], people=[NEAR])  # people around it all along
    out, _ = run(watch, t, t + 30 * 60, bags=[BAG])
    assert out == []


def test_the_live_picture_shows_bags_still_for_30_seconds():
    watch = watching()
    run(watch, 20, 48, bags=[BAG])
    assert watch.shown(48) == []
    run(watch, 48, 52, bags=[BAG])
    [(box, still)] = watch.shown(52)
    assert box == list(BAG) and 30 <= still <= 32


# ---- the person detector also finds bags --------------------------------------------------------------
class FakeBox:
    def __init__(self, cls, conf, xyxy):
        self.kind, self.score = cls, conf
        self.cls, self.conf, self.xyxy = [cls], [conf], [np.array(xyxy, float)]


class FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class FakeYolo:
    def __init__(self, boxes):
        self.boxes, self.calls = boxes, []

    def __call__(self, frame, classes, conf, imgsz, verbose):
        self.calls.append((classes, conf))
        return [FakeResult([b for b in self.boxes if b.kind in classes and b.score > conf])]


def fake_detector(boxes) -> DetectionService:
    svc = DetectionService("unused.pt")
    svc._model = FakeYolo(boxes)
    return svc


def test_bags_come_from_the_same_run_with_their_own_confidence():
    frame = np.zeros((720, 1280, 3), np.uint8)
    svc = fake_detector([
        FakeBox(0, 0.9, [100, 100, 300, 600]),
        FakeBox(0, 0.45, [400, 100, 600, 600]),   # below the person confidence
        FakeBox(0, 0.9, [700, 600, 720, 640]),    # a person too small to count
        FakeBox(28, 0.5, [800, 600, 840, 640]),   # a small suitcase: bags have no minimum size
        FakeBox(24, 0.37, [900, 500, 960, 580]),  # a backpack just above the bag confidence
        FakeBox(26, 0.3, [1000, 500, 1060, 580]),
    ])
    people, bags = svc.detect(frame, 0.5, 10, bags=True)
    assert svc._model.calls == [([0, *BAG_CLASSES], 0.35)]
    assert [p.bbox for p in people] == [[100, 100, 300, 600]] and people[0].class_name == "person"
    assert [(b.class_name, b.bbox) for b in bags] == [("suitcase", [800, 600, 840, 640]),
                                                      ("backpack", [900, 500, 960, 580])]
    # Without bags it is the same call as before
    assert svc.detect(frame, 0.5, 10) == (people, [])
    assert svc._model.calls[-1] == ([0], 0.5)
    assert svc.detect_persons(frame, 0.5, 10) == people


def _sample_pictures() -> list:
    try:
        import ultralytics
    except ImportError:
        return []
    folder = Path(ultralytics.__file__).parent / "assets"
    return [img for img in (cv2.imread(str(p)) for p in sorted(folder.glob("*.jpg"))) if img is not None]


@pytest.mark.parametrize("confidence,min_height", [(0.5, 10), (0.2, 0), (0.8, 40)])
def test_people_are_found_exactly_as_before_when_bags_are_looked_for(confidence, min_height):
    pictures = _sample_pictures()
    if not pictures or not detection_service.load():
        pytest.skip("needs the person detector and its sample pictures")
    found = 0
    for img in pictures:
        before = detection_service.detect_persons(img, confidence, min_height)
        after, _ = detection_service.detect(img, confidence, min_height, bags=True)
        assert [(p.bbox, p.confidence) for p in after] == [(p.bbox, p.confidence) for p in before]
        found += len(before)
    assert found or confidence == 0.8


# ---- falls -----------------------------------------------------------------------------------------
def pose(angle: float, head_below_hips=False, seen=0.9) -> np.ndarray:
    """Keypoints of a body whose shoulder-hip line is `angle` degrees from horizontal."""
    p = np.zeros((17, 3), np.float32)
    sx, sy = 300.0, 300.0
    dx, dy = 120 * math.cos(math.radians(angle)), 120 * math.sin(math.radians(angle))
    hx, hy = sx + dx, sy + dy
    head = (hx + 10, hy + 20) if head_below_hips else (sx - 0.4 * dx, sy - 0.4 * dy)
    p[0] = [*head, seen]
    p[5], p[6] = [sx - 6, sy, seen], [sx + 6, sy, seen]
    p[11], p[12] = [hx - 6, hy, seen], [hx + 6, hy, seen]
    return p


@pytest.mark.parametrize("points,expected", [
    (pose(0), True), (pose(20), True), (pose(35), True),
    (pose(45), None),                        # in between: can't tell
    (pose(60), False), (pose(88), False),
    (pose(80, head_below_hips=True), True),  # head at or below the hips
    (pose(10, seen=0.1), None),              # keypoints too unsure
    (None, None),
])
def test_lying_from_keypoints(points, expected):
    assert lying(points) is expected


WIDE = [100, 400, 420, 560]      # wider than tall: maybe lying
STANDING = [100, 100, 220, 560]


class Poses:
    """Gives the same keypoints for every box it is asked about, and counts the questions."""

    def __init__(self, points):
        self.points, self.asked = points, []

    def __call__(self, boxes):
        self.asked.append(len(boxes))
        return [self.points] * len(boxes)


def watch_fall(watch: FallWatch, poses: Poses, start: float, end: float, box=WIDE, key=1, step=0.5):
    out, t = [], start
    while t < end:
        out += watch.update([(key, list(box))], t, poses)
        t += step
    return out, t


def test_someone_lying_still_for_10_seconds_may_have_fallen():
    watch, poses = FallWatch(), Poses(pose(10))
    out, t = watch_fall(watch, poses, 0, 9.9)
    assert out == []
    out, t = watch_fall(watch, poses, t, t + 1.5)
    [fallen] = out
    assert fallen.kind == "fallen" and 10 <= fallen.seconds < 11.5 and len(watch.fallen) == 1
    assert watch_fall(watch, poses, t, t + 20)[0] == [], "reported once"


def test_moving_about_on_the_floor_is_not_a_fall():
    watch, poses = FallWatch(), Poses(pose(10))
    out, t = [], 0.0
    for i in range(40):
        x = 100 + i * 40  # crawling: more than half their length every few seconds
        out += watch.update([(1, [x, 400, x + 320, 560])], t, poses)
        t += 0.5
    assert out == []


def test_getting_up_ends_it():
    watch, lying_down = FallWatch(), Poses(pose(5))
    out, t = watch_fall(watch, lying_down, 0, 12)
    assert [h.kind for h in out] == ["fallen"]
    upright = Poses(pose(85))
    out, t = watch_fall(watch, upright, t, t + 1.0, box=STANDING)
    assert out == [], "one upright look isn't enough"
    out, t = watch_fall(watch, upright, t, t + 1.5, box=STANDING)
    assert [h.kind for h in out] == ["up"] and watch.fallen == []


def test_someone_who_fell_and_is_no_longer_seen():
    watch, poses = FallWatch(), Poses(pose(5))
    watch_fall(watch, poses, 0, 12)
    out = watch.update([], 12 + 31, poses)
    assert [h.kind for h in out] == ["gone"]


def test_the_detector_losing_someone_on_the_floor_keeps_the_count():
    watch, poses = FallWatch(), Poses(pose(5))
    watch_fall(watch, poses, 0, 6, key=1)
    out, _ = watch_fall(watch, poses, 8, 12, key=7)  # found again as a new track in the same place
    assert [h.kind for h in out] == ["fallen"]


def test_pose_is_only_checked_for_people_who_look_possibly_down():
    watch, poses = FallWatch(), Poses(pose(85))
    watch_fall(watch, poses, 0, 10, box=STANDING)
    assert poses.asked == [], "someone standing is never looked at"

    # The top of the box drops by half its height within a second, though the box is still taller than wide
    watch = FallWatch()
    watch.update([(1, [100, 100, 220, 560])], 0, poses)
    watch.update([(1, [100, 330, 260, 560])], 1.0, poses)
    assert poses.asked == [1]

    # Sitting down slowly is no sudden drop
    watch, poses = FallWatch(), Poses(pose(85))
    for i, t in enumerate(range(0, 6)):
        watch.update([(1, [100, 100 + 46 * i, 260, 560])], float(t), poses)
    assert poses.asked == []


def test_pose_checks_run_at_most_once_a_second():
    watch, poses = FallWatch(), Poses(pose(85))
    watch_fall(watch, poses, 0, 5, step=0.1)
    assert len(poses.asked) == 5 and watch.checks == 5


# ---- watch spots -------------------------------------------------------------------------------------
GATE = [[0.6, 0.3], [0.8, 0.3], [0.8, 0.8], [0.6, 0.8]]
GATE_PX = (slice(108, 288), slice(384, 512))  # rows, columns of a 640x360 picture


def scene(seed=1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.integers(40, 200, (36, 64, 3), dtype=np.uint8)
    return cv2.resize(noise, (640, 360), interpolation=cv2.INTER_CUBIC)


def opened(frame) -> np.ndarray:
    out = frame.copy()
    out[GATE_PX] = scene(seed=2)[GATE_PX]  # something else shows where the gate was
    return out


def feed(watch: SpotWatch, frame, start, end, people=(), step=0.5):
    out, t = [], start
    while t < end:
        out += watch.update(frame, [list(p) for p in people], t)
        t += step
    return out, t


def test_a_spot_that_changes_and_stays_changed_counts():
    watch, calm = SpotWatch([("Back gate", GATE)]), scene()
    out, t = feed(watch, calm, 0, 10)
    assert out == [] and watch.spots[0].before is not None
    out, t = feed(watch, opened(calm), t, 14.5)
    assert out == [], "not before it has stayed changed for 5 s"
    out, t = feed(watch, opened(calm), t, 16)
    [changed] = out
    assert changed.kind == "changed" and changed.since == 10 and watch.spots[0].changed
    assert changed.spot.before_change[0] < 10, "the picture from before the change is kept"
    out, t = feed(watch, calm, t, t + 4)
    assert out == [], "back to normal also needs 5 s"
    out, t = feed(watch, calm, t, t + 2)
    assert [h.kind for h in out] == ["normal"] and not watch.spots[0].changed


def test_a_brief_change_does_not_count():
    watch, calm = SpotWatch([("Back gate", GATE)]), scene()
    feed(watch, calm, 0, 10)
    out, t = feed(watch, opened(calm), 10, 13)  # a car passing
    out2, _ = feed(watch, calm, t, 30)
    assert out + out2 == []


@pytest.mark.parametrize("change", [
    lambda f: (f * 0.55).astype(np.uint8),                         # lights dimmed
    lambda f: np.clip(f.astype(int) + 45, 0, 255).astype(np.uint8),  # brighter
])
def test_a_lighting_change_across_the_whole_picture_does_not_count(change):
    watch, calm = SpotWatch([("Back gate", GATE)]), scene()
    feed(watch, calm, 0, 10)
    out, _ = feed(watch, change(calm), 10, 40)
    assert out == [] and not watch.spots[0].changed


def test_when_the_whole_picture_changes_references_are_taken_again():
    watch, calm = SpotWatch([("Back gate", GATE)]), scene()
    feed(watch, calm, 0, 10)
    moved = scene(seed=5)  # the camera was turned
    out, t = feed(watch, moved, 10, 40)
    assert out == []
    out, _ = feed(watch, moved, t, 60)
    assert out == [] and not watch.spots[0].changed


def test_a_person_in_the_spot_does_not_count():
    watch, calm = SpotWatch([("Back gate", GATE)]), scene()
    feed(watch, calm, 0, 10)
    person = [800, 200, 1000, 700]  # frame pixels of a 1280 wide picture: inside the gate
    big = cv2.resize(opened(calm), (1280, 720))
    out, t = feed(watch, big, 10, 30, people=[person])
    assert out == [], "someone standing in the gate"
    out, t = feed(watch, big, t, t + 9)  # once nobody has been inside for a while, 5 s
    [changed] = out
    assert changed.kind == "changed" and changed.since == 10, "they left it open"


def test_marking_a_changed_spot_as_normal():
    watch, calm = SpotWatch([("Back gate", GATE)]), scene()
    feed(watch, calm, 0, 10)
    feed(watch, opened(calm), 10, 16)
    assert watch.accept() == ["Back gate"]
    out, _ = feed(watch, opened(calm), 16, 40)
    assert out == [] and not watch.spots[0].changed, "its new look is its normal now"


# ---- the sentinel: events, pictures, alerts ----------------------------------------------------------
class FakePoses:
    def __init__(self, points):
        self.points, self.loaded, self.asked = points, True, 0

    def ready(self):
        return self.loaded

    def estimate(self, frame, boxes):
        self.asked += 1
        return [self.points] * len(boxes)


@pytest.fixture
def sentinel(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    s = Sentinel(lambda: "Porch", FakeSpeaker(), FakeRecorder(), settings=settings, notifier=FakeNotifier(),
                 events=FakeEvents(), poses=FakePoses(pose(5)), submit=lambda job: job())
    return s


def person(box, track=1) -> Detection:
    return Detection(confidence=0.9, bbox=list(box), track_id=track)


def frame() -> np.ndarray:
    return cv2.resize(scene(), (W, H))


def detect_for(s: Sentinel, start, end, people=(), bags=(), step=2.0):
    t, f = start, frame()
    while t < end:
        found = [person(p, i + 1) for i, p in enumerate(people)]
        s.detected(f, found, found, [bag(b) for b in bags], t)
        t += step
    return t


def kinds(s: Sentinel):
    return [(e[0], e[1]) for e in s.events.entries]


def jpeg_size(data: bytes) -> tuple[int, int]:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return img.shape[1], img.shape[0]


def test_left_bag_is_logged_with_a_picture_and_alerted_while_armed(sentinel):
    t = detect_for(sentinel, 0, 20)
    t = detect_for(sentinel, t, t + LIMIT + 2, bags=[BAG])
    [(kind, text)] = sentinel.events.descriptions[:1]
    assert kind == "UNATTENDED" and text == "Bag left unattended on Porch for 2 min"
    assert kinds(sentinel) == [("UNATTENDED", "MEDIUM"), ("ALERT", "MEDIUM")]
    picture = sentinel.events.pictures[0][1]
    assert jpeg_size(picture) == (960, 540)
    assert sentinel.notifier.alerts == ["Bag left at Porch"]
    assert sentinel.status([])["left_bags"] == 1

    t = detect_for(sentinel, t, t + 20)
    assert sentinel.events.descriptions[-1] == ("UNATTENDED", "The bag on Porch was picked up")
    assert sentinel.events.entries[-1][1] == "INFO" and sentinel.events.pictures[-1][1]
    assert sentinel.status([])["left_bags"] == 0

    detect_for(sentinel, t, t + LIMIT + 4, bags=[(200, 500, 260, 580)])
    assert len(sentinel.notifier.alerts) == 1, "alerts are rate limited; the event is still logged"
    assert [e for e in kinds(sentinel) if e[0] == "UNATTENDED"] == [("UNATTENDED", "MEDIUM"), ("UNATTENDED", "INFO"),
                                                                    ("UNATTENDED", "MEDIUM")]


def test_left_bag_is_only_logged_while_disarmed(sentinel):
    sentinel.settings.update({"armed": False})
    t = detect_for(sentinel, 0, 20)
    detect_for(sentinel, t, t + LIMIT + 2, bags=[BAG])
    assert kinds(sentinel) == [("UNATTENDED", "MEDIUM")] and sentinel.notifier.alerts == []


def test_unattended_bags_can_be_turned_off(sentinel):
    sentinel.settings.update({"detection": {"unattended_minutes": 0}})
    t = detect_for(sentinel, 0, 20)
    detect_for(sentinel, t, t + 600, bags=[BAG])
    assert sentinel.events.entries == []


def fall_for(s: Sentinel, start, end, box=WIDE, step=0.5):
    t, f = start, frame()
    while t < end:
        s.detected(f, [person(box)], [person(box)], [], t)
        t += step
    return t


def test_a_fall_alerts_even_while_disarmed_and_asks_if_they_are_ok(sentinel):
    sentinel.settings.update({"armed": False})
    t = fall_for(sentinel, 0, 11)
    assert kinds(sentinel) == [("FALL", "HIGH"), ("ALERT", "HIGH"), ("VOICE", "INFO")]
    assert sentinel.events.descriptions[0] == ("FALL", "Someone may have fallen on Porch and hasn't got up for 10 s")
    assert sentinel.events.pictures[0][1] and sentinel.notifier.alerts == ["Possible fall at Porch"]
    assert sentinel.speaker.said == [ASK_TEXT]
    assert sentinel.status([])["fallen"] == 1

    sentinel.poses.points = pose(85)
    fall_for(sentinel, t, t + 3, box=STANDING)
    assert sentinel.events.descriptions[-1] == ("FALL", "Someone who may have fallen on Porch is up again")
    assert sentinel.status([])["fallen"] == 0


def test_nothing_is_said_unless_the_owner_really_was_alerted(sentinel):
    sentinel.notifier.works = False  # no alert channel set up
    fall_for(sentinel, 0, 11)
    assert kinds(sentinel) == [("FALL", "HIGH")] and sentinel.speaker.said == []


def test_fall_settings(sentinel):
    sentinel.settings.update({"detection": {"fall_ask": False}})
    fall_for(sentinel, 0, 11)
    assert kinds(sentinel) == [("FALL", "HIGH"), ("ALERT", "HIGH")] and sentinel.speaker.said == []

    sentinel.settings.update({"detection": {"fall_alerts": "log", "fall_ask": True}})
    sentinel.falls.reset()
    sentinel.events.entries.clear()
    fall_for(sentinel, 100, 111)
    assert kinds(sentinel) == [("FALL", "HIGH")] and sentinel.speaker.said == []

    sentinel.settings.update({"detection": {"fall_alerts": "off"}})
    sentinel.events.entries.clear()
    asked = sentinel.poses.asked
    fall_for(sentinel, 200, 230)
    assert sentinel.events.entries == [] and sentinel.poses.asked == asked, "the pose model isn't even asked"


def test_falls_wait_for_the_pose_model(sentinel):
    sentinel.poses.loaded = False
    fall_for(sentinel, 0, 30)
    assert sentinel.events.entries == [] and sentinel.poses.asked == 0


def test_a_changed_watch_spot_is_logged_with_before_and_after(sentinel):
    spots = [CameraConfig(id="c", watch_spots=[{"name": "Back gate", "polygon": GATE}]).watch_spots[0]]
    calm = cv2.resize(scene(), (W, H))
    gate = cv2.resize(opened(scene()), (W, H))
    t = 0.0
    while t < 10:
        sentinel.watch(calm, t, spots)
        t += 0.5
    while t < 16:
        sentinel.watch(gate, t, spots)
        t += 0.5
    since = datetime.fromtimestamp(10).strftime("%H:%M")
    assert sentinel.events.descriptions[0] == ("SPOT", f"Back gate looks different (opened or moved) since {since}")
    assert kinds(sentinel) == [("SPOT", "MEDIUM"), ("ALERT", "MEDIUM")]
    assert jpeg_size(sentinel.events.pictures[0][1]) == (964, 270), "before and after, side by side"
    assert sentinel.status(spots)["changed_spots"] == [{"name": "Back gate", "since": 10.0}]

    # Drawn on the live picture, in amber while changed
    live = gate.copy()
    sentinel.draw(live, t)
    assert (live != gate).any()

    assert sentinel.accept_spots() == ["Back gate"]
    sentinel.watch(gate, t, spots)
    assert sentinel.events.descriptions[-1][0] == "SPOT" and "counts as normal" in sentinel.events.descriptions[-1][1]
    assert sentinel.status(spots)["changed_spots"] == []


def test_a_changed_watch_spot_is_not_alerted_while_disarmed(sentinel):
    sentinel.settings.update({"armed": False})
    spots = [CameraConfig(id="c", watch_spots=[{"name": "Gate", "polygon": GATE}]).watch_spots[0]]
    calm, gate = cv2.resize(scene(), (W, H)), cv2.resize(opened(scene()), (W, H))
    for i in range(20):
        sentinel.watch(calm, i * 0.5, spots)
    for i in range(20, 34):
        sentinel.watch(gate, i * 0.5, spots)
    for i in range(34, 50):
        sentinel.watch(calm, i * 0.5, spots)
    assert kinds(sentinel) == [("SPOT", "MEDIUM"), ("SPOT", "INFO")]
    assert sentinel.events.descriptions[-1] == ("SPOT", "Gate is back to normal")


def test_bags_drawn_on_the_live_picture(sentinel):
    t = detect_for(sentinel, 0, 20)
    detect_for(sentinel, t, t + 40, bags=[BAG])
    live = np.zeros((H, W, 3), np.uint8)
    sentinel.draw(live, t + 40)
    assert tuple(live[540, 600]) == (0, 170, 240), "amber outline"


def test_before_and_after_without_an_earlier_picture():
    img = before_after(None, frame(), 0, GATE, "Porch")
    assert jpeg_size(img) == (480, 270)


# ---- settings and the API -------------------------------------------------------------------------
def test_sentinel_settings_defaults_and_limits(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    det = svc.get().detection
    assert (det.unattended_minutes, det.fall_alerts, det.fall_ask) == (2, "alert", True)
    for bad in ({"unattended_minutes": -1}, {"unattended_minutes": 61}, {"fall_alerts": "maybe"}):
        with pytest.raises(SettingsError):
            svc.update({"detection": bad})
    svc.update({"detection": {"unattended_minutes": 0, "fall_alerts": "log", "fall_ask": False}})
    det = SettingsService(tmp_path / "settings.json").get().detection
    assert (det.unattended_minutes, det.fall_alerts, det.fall_ask) == (0, "log", False)


@pytest.mark.parametrize("spots,message", [
    ([{"name": "  ", "polygon": GATE}], "at least 1 character"),
    ([{"name": "Gate", "polygon": GATE}, {"name": "gate", "polygon": GATE}], "Each watch spot needs its own name"),
    ([{"name": f"Spot {i}", "polygon": GATE} for i in range(9)], "At most 8 watch spots"),
    ([{"name": "Gate", "polygon": [[0, 0], [1, 1]]}], "A watch spot needs 3 to 32 corners"),
    ([{"name": "Gate", "polygon": [[0, 0], [1, 1], [1, 0], [0, 1]]}], "watch spot's outline must not cross"),
    ([{"name": "Gate", "polygon": [[0, 0], [0.01, 0], [0, 0.01]]}], "A watch spot is too small"),
])
def test_invalid_watch_spots_are_rejected(tmp_path, spots, message):
    svc = SettingsService(tmp_path / "settings.json")
    with pytest.raises(SettingsError, match=message):
        svc.update_camera("cam1", {"watch_spots": spots})


def test_watch_spots_are_saved_with_tidy_names(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    svc.update_camera("cam1", {"watch_spots": [{"name": "  Back   gate ", "polygon": GATE}]})
    [spot] = SettingsService(tmp_path / "settings.json").get().camera("cam1").watch_spots
    assert spot.name == "Back gate" and spot.polygon == GATE


@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


def test_watch_spots_through_the_api(client):
    r = client.patch("/api/cameras/cam1", json={"watch_spots": [{"name": "Back gate", "polygon": GATE}]})
    assert r.status_code == 200 and r.json()["watch_spots"] == [{"name": "Back gate", "polygon": GATE}]
    cam = next(c for c in client.get("/api/cameras").json()["cameras"] if c["id"] == "cam1")
    assert cam["watch_spots"][0]["name"] == "Back gate" and cam["zones"] == []
    r = client.patch("/api/cameras/cam1", json={"watch_spots": [{"name": "Gate", "polygon": [[0, 0], [1, 1]]}]})
    assert r.status_code == 422 and "watch spot" in r.json()["detail"]

    status = next(c for c in client.get("/api/status").json()["cameras"] if c["id"] == "cam1")
    assert status["sentinel"] == {"spots": 1, "changed_spots": [], "left_bags": 0, "fallen": 0}
    assert client.get("/api/system").json()["pose"]["state"] in ("idle", "loading", "ready", "error")
    r = client.post("/api/cameras/cam1/watch-spots/accept")
    assert r.status_code == 409
    assert client.post("/api/cameras/nope/watch-spots/accept").status_code == 404
    assert client.patch("/api/cameras/cam1", json={"watch_spots": []}).json()["watch_spots"] == []


def test_sentinel_settings_through_the_api(client):
    r = client.patch("/api/settings", json={"detection": {"unattended_minutes": 5, "fall_alerts": "log"}})
    assert r.status_code == 200 and r.json()["detection"]["unattended_minutes"] == 5
    assert client.patch("/api/settings", json={"detection": {"unattended_minutes": 99}}).status_code == 422
    client.patch("/api/settings", json={"detection": {"unattended_minutes": 2, "fall_alerts": "alert"}})


# ---- the camera pipeline ------------------------------------------------------------------------------
class PeopleAndBags:
    error = None

    def __init__(self):
        self.calls = []

    def detect(self, frame, confidence, min_height, bags=False):
        self.calls.append(bags)
        return [Detection(confidence=0.9, bbox=[100, 100, 300, 600])], \
            [bag((700, 600, 760, 680)), bag((1000, 100, 1060, 180))] if bags else []


def test_camera_finds_bags_only_where_its_zones_are(tmp_path):
    from services.camera_service import CameraUnit
    from test_brain import FakeEvents as Events
    settings = SettingsService(tmp_path / "settings.json")
    settings.update({"armed": False, "detection": {"face_recognition": False}})
    settings.update_camera("cam1", {"zones": [[[0, 0.5], [1, 0.5], [1, 1], [0, 1]]]})  # the bottom half
    detector = PeopleAndBags()
    unit = CameraUnit(settings.get().camera("cam1"), settings, detector=detector, events=Events())
    unit.sentinel.submit = lambda job: job()
    picture = np.zeros((H, W, 3), np.uint8)
    for i in range(5):
        unit._detect(picture, 100.0 + i, settings.get(), False)
    assert detector.calls == [True] * 5
    assert [b.bbox for b in unit.sentinel.bags.bags.values()] == [[700, 600, 760, 680]], "by where it stands"
    settings.update({"detection": {"unattended_minutes": 0}})
    unit._detect(picture, 106.0, settings.get(), False)
    assert detector.calls[-1] is False and unit.sentinel.bags.bags == {}
