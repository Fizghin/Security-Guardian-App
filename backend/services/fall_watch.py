"""
Someone who may have fallen and not got up.

Body keypoints are only looked at for people whose box suggests they might be
down: wider than tall, or whose top dropped by 40% of their height within 1.5 s.
That check runs at most once a second per camera. A person counts as lying when
the line from their shoulders to their hips is within 35 degrees of horizontal,
or their head is at or below their hips. Lying for 10 s without moving much
means they may have fallen; looking upright twice in a row means they are up.
"""
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from services.tracker import iou

WIDE_RATIO = 1.2          # a box this much wider than tall may be someone lying down
DROP_SHARE = 0.4          # ...and so may one whose top dropped by this share of its height
DROP_SECONDS = 1.5        # ...this quickly
WATCH_AFTER_DROP = 20.0   # seconds a sudden drop keeps someone checked, whatever their box looks like
CHECK_EVERY = 1.0         # at most one pose check a second per camera
LYING_DEGREES = 35.0      # shoulder-hip line at most this far from horizontal: lying
UPRIGHT_DEGREES = 55.0    # at least this far: upright
FALLEN_SECONDS = 10.0
MOVE_SHARE = 0.5          # moving more than half their length counts as moving, not lying still
UP_CHECKS = 2             # upright this many checks in a row: up again
FORGET_SECONDS = 30.0     # people not seen for this long are forgotten
KEYPOINT_CONFIDENCE = 0.3

SHOULDERS, HIPS, HEAD = (5, 6), (11, 12), (0, 1, 2, 3, 4)

Estimator = Callable[[list[list[float]]], list[np.ndarray | None]]


def _mean_point(points: np.ndarray, indices) -> tuple[float, float] | None:
    seen = [points[i] for i in indices if points[i][2] >= KEYPOINT_CONFIDENCE]
    if not seen:
        return None
    return float(np.mean([p[0] for p in seen])), float(np.mean([p[1] for p in seen]))


def body_angle(points: np.ndarray) -> float | None:
    """Degrees between the shoulder-hip line and horizontal (0 = lying flat, 90 = upright), or None
    when the shoulders or hips can't be seen, or are too close together to tell a direction."""
    shoulders, hips = _mean_point(points, SHOULDERS), _mean_point(points, HIPS)
    if shoulders is None or hips is None:
        return None
    dx, dy = hips[0] - shoulders[0], hips[1] - shoulders[1]
    seen = points[points[:, 2] >= KEYPOINT_CONFIDENCE]
    size = max(np.ptp(seen[:, 0]), np.ptp(seen[:, 1])) if len(seen) else 0.0
    if math.hypot(dx, dy) < max(4.0, 0.15 * size):
        return None
    return math.degrees(math.atan2(abs(dy), abs(dx)))


def lying(points: np.ndarray | None) -> bool | None:
    """True when the pose looks like someone lying down, False when clearly upright, None when the
    keypoints don't say."""
    if points is None:
        return None
    angle = body_angle(points)
    head, hips = _mean_point(points, HEAD), _mean_point(points, HIPS)
    head_low = head is not None and hips is not None and head[1] >= hips[1]  # image y grows downwards
    if (angle is not None and angle <= LYING_DEGREES) or head_low:
        return True
    if angle is not None and angle >= UPRIGHT_DEGREES:
        return False
    return None


@dataclass
class Person:
    key: int
    bbox: list[float]
    last_seen: float
    tops: deque = field(default_factory=lambda: deque(maxlen=16))  # (time, top, height)
    dropped_at: float | None = None
    down_since: float | None = None
    anchor: tuple[float, float] = (0.0, 0.0)
    fallen: bool = False
    upright: int = 0

    def possibly_down(self, now: float) -> bool:
        x1, y1, x2, y2 = self.bbox
        wide = (x2 - x1) >= WIDE_RATIO * max(1.0, y2 - y1)
        dropped = self.dropped_at is not None and now - self.dropped_at <= WATCH_AFTER_DROP
        return wide or dropped or self.down_since is not None


@dataclass
class Happening:
    kind: str  # "fallen" | "up" | "gone"
    bbox: list[float]
    seconds: float = 0.0


def _centre(box: list[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


class FallWatch:
    def __init__(self):
        self.people: dict[int, Person] = {}
        self.last_check = float("-inf")
        self.checks = 0  # pose runs, for tests and diagnostics

    def reset(self) -> None:
        self.people.clear()

    @property
    def fallen(self) -> list[Person]:
        return [p for p in self.people.values() if p.fallen]

    def _person(self, key: int, box: list[float], now: float, in_view: set[int]) -> Person:
        person = self.people.get(key)
        if person is None:
            # Someone lying still is easily lost by the detector for a moment and then comes back as a
            # new track: they keep their state when they reappear in the same place.
            old = max((p for p in self.people.values() if p.key not in in_view), key=lambda p: iou(p.bbox, box),
                      default=None)
            if old is not None and iou(old.bbox, box) >= 0.3:
                del self.people[old.key]
                old.key = key
                person = old
            else:
                person = Person(key, box, now)
            self.people[key] = person
        return person

    def update(self, people: list[tuple[int, list[float]]], now: float, estimate: Estimator | None) -> list[Happening]:
        """people: (track id, box) of everyone in view. estimate: gives keypoints for boxes, or None while
        no pose model is available (then nothing is decided)."""
        out: list[Happening] = []
        in_view = {key for key, _ in people}
        for key, box in people:
            person = self._person(key, list(box), now, in_view)
            person.bbox, person.last_seen = list(box), now
            top, height = box[1], box[3] - box[1]
            for t, old_top, old_height in person.tops:
                if now - t <= DROP_SECONDS and top - old_top >= DROP_SHARE * old_height:
                    person.dropped_at = now
                    break
            person.tops.append((now, top, height))

        for key in [k for k, p in self.people.items() if now - p.last_seen > FORGET_SECONDS]:
            person = self.people.pop(key)
            if person.fallen:
                out.append(Happening("gone", person.bbox))

        due = [p for p in self.people.values() if p.key in in_view and p.possibly_down(now)]
        if not due or estimate is None or now - self.last_check < CHECK_EVERY:
            return out
        self.last_check = now
        self.checks += 1
        for person, points in zip(due, estimate([p.bbox for p in due])):
            out.extend(self._judge(person, lying(points), now))
        return out

    def _judge(self, person: Person, down: bool | None, now: float) -> list[Happening]:
        if down is None:
            return []  # can't tell: nothing changes
        if not down:
            person.upright += 1
            if person.fallen and person.upright < UP_CHECKS:
                return []
            was_fallen = person.fallen
            person.down_since, person.fallen, person.dropped_at = None, False, None
            return [Happening("up", person.bbox)] if was_fallen else []
        person.upright = 0
        centre = _centre(person.bbox)
        length = max(person.bbox[2] - person.bbox[0], person.bbox[3] - person.bbox[1])
        if person.down_since is None or math.dist(centre, person.anchor) > MOVE_SHARE * length:
            person.down_since, person.anchor = now, centre  # just went down, or moving: start counting again
            return []
        if not person.fallen and now - person.down_since >= FALLEN_SECONDS:
            person.fallen = True
            return [Happening("fallen", person.bbox, now - person.down_since)]
        return []
