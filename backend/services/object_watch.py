"""
Unattended objects: a bag or suitcase that stays in one place with nobody near it.

Each camera keeps a short list of the objects it sees. An object that is seen again
roughly where it was (overlapping boxes) is the same object; one that moves more than a
little starts its clock again. Once an object has stayed put for the configured time
with no person close by, it is reported once, with a picture. It is forgotten after it
has not been seen for a while, so a bag that was picked up and put down again later is
reported again.
"""
import time
from dataclasses import dataclass

from models.domain import Detection
from services.tracker import iou

FORGET_SECONDS = 30.0   # tolerates people walking in front of it
MOVE_FRACTION = 0.05    # of the picture width: moving further than this restarts its clock
NEAR_FACTOR = 1.5       # a person within this many object-widths is "with" the object
LABELS = {"backpack": "Backpack", "handbag": "Bag", "suitcase": "Suitcase"}


@dataclass
class WatchedObject:
    id: int
    kind: str
    bbox: list[float]
    anchor: tuple[float, float]  # centre when its clock (re)started
    still_since: float
    last_seen: float
    alone_since: float | None = None
    reported: bool = False
    confidence: float = 0.0
    hits: int = 1

    def still_seconds(self, now: float) -> float:
        return now - self.still_since

    def alone_seconds(self, now: float) -> float:
        return 0.0 if self.alone_since is None else now - self.alone_since


def _centre(b: list[float]) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _near(obj: list[float], person: list[float]) -> bool:
    ox, oy = _centre(obj)
    px, py = (person[0] + person[2]) / 2, person[3]  # where the person stands
    reach = NEAR_FACTOR * max(obj[2] - obj[0], obj[3] - obj[1], 1.0) + (person[2] - person[0]) / 2
    return abs(ox - px) <= reach and abs(oy - py) <= reach * 1.5


class UnattendedWatcher:
    def __init__(self):
        self.objects: dict[int, WatchedObject] = {}
        self._next = 1

    def reset(self) -> None:
        self.objects.clear()

    def update(self, things: list[Detection], persons: list[Detection], frame_w: int, now: float | None = None,
               after_seconds: float = 120.0) -> list[WatchedObject]:
        """Returns objects that have just become unattended (each one once)."""
        now = time.time() if now is None else now
        for oid in [i for i, o in self.objects.items() if now - o.last_seen > FORGET_SECONDS]:
            del self.objects[oid]

        used: set[int] = set()
        for d in things:
            best, score = None, 0.3
            for o in self.objects.values():
                if o.id in used or o.kind != d.class_name:
                    continue
                s = iou(o.bbox, d.bbox)
                if s > score:
                    best, score = o, s
            if best is None:
                # Moved further than its own size since last seen: still the same object if it is close by.
                cx, cy = _centre(d.bbox)
                size = max(d.bbox[2] - d.bbox[0], d.bbox[3] - d.bbox[1], 1.0)
                close = [o for o in self.objects.values() if o.id not in used and o.kind == d.class_name
                         and abs(_centre(o.bbox)[0] - cx) <= 2 * size and abs(_centre(o.bbox)[1] - cy) <= 2 * size]
                best = min(close, key=lambda o: abs(_centre(o.bbox)[0] - cx) + abs(_centre(o.bbox)[1] - cy),
                           default=None)
            if best is None:
                best = WatchedObject(self._next, d.class_name, d.bbox, _centre(d.bbox), now, now)
                self.objects[best.id] = best
                self._next += 1
            else:
                cx, cy = _centre(d.bbox)
                if max(abs(cx - best.anchor[0]), abs(cy - best.anchor[1])) > MOVE_FRACTION * frame_w:
                    best.anchor, best.still_since, best.alone_since, best.reported = (cx, cy), now, None, False
                best.bbox, best.last_seen = d.bbox, now
                best.hits += 1
            best.confidence = d.confidence
            used.add(best.id)

        fired: list[WatchedObject] = []
        people = [p.bbox for p in persons]
        for o in self.objects.values():
            if o.id not in used:
                continue  # hidden right now: keep its clocks, judge it when seen again
            if any(_near(o.bbox, p) for p in people):
                o.alone_since = None
            elif o.alone_since is None:
                o.alone_since = now
            if (not o.reported and o.hits >= 3 and o.still_seconds(now) >= after_seconds
                    and o.alone_seconds(now) >= min(after_seconds, 30.0)):
                o.reported = True
                fired.append(o)
        return fired

    def flagged(self, now: float | None = None) -> list[WatchedObject]:
        """Objects reported as unattended that are still in view, for drawing on the picture."""
        now = time.time() if now is None else now
        return [o for o in self.objects.values() if o.reported and now - o.last_seen < 2.0]


def describe(o: WatchedObject, now: float | None = None) -> str:
    now = time.time() if now is None else now
    minutes = max(1, round(o.still_seconds(now) / 60))
    return f"{LABELS.get(o.kind, o.kind.title())} left unattended for {minutes} min"
