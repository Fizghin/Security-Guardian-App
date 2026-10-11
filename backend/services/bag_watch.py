"""
Bags left behind: backpacks, handbags and suitcases that stay put with nobody near them.

Bags get their own small tracker. A bag is left once it has stood still (moved
less than 3% of the picture) for the set number of minutes and no person has
overlapped it or come within about one bag width of it for the last 30 s. Bags
that were there when watching started, or that stood still for over 10 minutes
without being left (people were around them all along), are part of the scene
and never count, also when the detector loses them for a while and finds them
again in the same place. A left bag that is gone for 10 s, while nobody stands
where it was, was picked up.
"""
from dataclasses import dataclass

from models.domain import Detection
from services.tracker import iou

STILL_SHARE = 0.03      # moved less than this share of the picture's width and height: still
NEAR_SECONDS = 30.0     # nobody may have been near a bag for this long before it counts as left
SCENE_SECONDS = 600.0   # still this long without being left: part of the scene
START_SECONDS = 10.0    # bags seen this soon after watching starts were already there
GONE_SECONDS = 10.0     # a left bag missing this long, with nobody where it stood, was picked up
LOST_SECONDS = 60.0     # other bags are forgotten after this long unseen
SCENE_MEMORY = 3600.0   # how long the places of scene bags are remembered
SHOW_STILL = 30.0       # the live picture shows bags still for this long


@dataclass
class Bag:
    id: int
    bbox: list[float]
    first_seen: float
    last_seen: float
    still_since: float
    anchor: list[float]
    person_near: float | None = None
    scene: bool = False
    left_at: float | None = None
    missing_since: float | None = None


@dataclass
class Happening:
    kind: str  # "left" | "picked_up"
    bag: Bag
    seconds: float = 0.0  # left: how long it has been unattended


def _overlaps(a: list[float], b: list[float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _near(bag: list[float], person: list[float]) -> bool:
    """The person overlaps the bag or is within about one bag width of it."""
    reach = bag[2] - bag[0]
    return _overlaps([bag[0] - reach, bag[1] - reach, bag[2] + reach, bag[3] + reach], person)


class BagWatch:
    def __init__(self):
        self.bags: dict[int, Bag] = {}
        self._next_id = 1
        self.started: float | None = None
        self._scene_places: list[tuple[list[float], float]] = []  # (box, until)

    def reset(self) -> None:
        self.bags.clear()
        self.started = None
        self._scene_places.clear()

    @property
    def left(self) -> list[Bag]:
        return [b for b in self.bags.values() if b.left_at is not None]

    def shown(self, now: float) -> list[tuple[list[float], float]]:
        """(box, seconds still) of the bags to draw on the live picture."""
        return [(b.bbox, now - b.still_since) for b in self.bags.values()
                if not b.scene and now - b.last_seen <= GONE_SECONDS and now - b.still_since >= SHOW_STILL]

    def _match(self, detections: list[Detection]) -> list[Bag | None]:
        pairs = sorted(((iou(d.bbox, b.bbox), i, b.id) for i, d in enumerate(detections) for b in self.bags.values()),
                       reverse=True)
        out: list[Bag | None] = [None] * len(detections)
        used: set[int] = set()
        for score, i, bag_id in pairs:
            if score < 0.3:
                break
            if out[i] is None and bag_id not in used:
                out[i] = self.bags[bag_id]
                used.add(bag_id)
        return out

    def update(self, detections: list[Detection], people: list[list[float]], now: float, width: int, height: int,
               limit_seconds: float) -> list[Happening]:
        """detections: bags in the zones; people: boxes of everyone in view. Returns what happened."""
        if self.started is None:
            self.started = now
        self._scene_places = [(box, until) for box, until in self._scene_places if until > now]
        out: list[Happening] = []
        seen: set[int] = set()
        for det, bag in zip(detections, self._match(detections)):
            if bag is None:
                bag = Bag(self._next_id, det.bbox, now, now, now, list(det.bbox))
                self._next_id += 1
                bag.scene = now - self.started <= START_SECONDS or any(
                    iou(box, det.bbox) >= 0.5 for box, _ in self._scene_places)
                self.bags[bag.id] = bag
            seen.add(bag.id)
            bag.bbox, bag.last_seen, bag.missing_since = list(det.bbox), now, None
            (ax, ay), (cx, cy) = self._centre(bag.anchor), self._centre(bag.bbox)
            if abs(cx - ax) > STILL_SHARE * width or abs(cy - ay) > STILL_SHARE * height:
                if bag.left_at is not None:
                    out.append(Happening("picked_up", bag))  # someone moved it
                bag.anchor, bag.still_since, bag.left_at = list(bag.bbox), now, None

        for bag in self.bags.values():
            if any(_near(bag.bbox, p) for p in people):
                bag.person_near = now

        scene_after = max(SCENE_SECONDS, 2 * limit_seconds)
        for bag in list(self.bags.values()):
            if bag.id not in seen:
                if bag.left_at is not None:
                    if any(_overlaps(bag.bbox, p) for p in people):
                        bag.missing_since = None  # someone may just be standing in front of it
                    elif bag.missing_since is None:
                        bag.missing_since = now
                    elif now - bag.missing_since >= GONE_SECONDS:
                        del self.bags[bag.id]
                        out.append(Happening("picked_up", bag))
                elif now - bag.last_seen > LOST_SECONDS:
                    del self.bags[bag.id]
                    if bag.scene:
                        self._scene_places.append((bag.bbox, now + SCENE_MEMORY))
                continue
            if bag.scene or bag.left_at is not None:
                continue
            still = now - bag.still_since
            if still > scene_after:
                bag.scene = True
                continue
            attended = bag.person_near is not None and now - bag.person_near < NEAR_SECONDS
            if limit_seconds > 0 and still >= limit_seconds and not attended:
                bag.left_at = now
                out.append(Happening("left", bag, now - max(bag.still_since, bag.person_near or bag.still_since)))
        return out

    @staticmethod
    def _centre(box: list[float]) -> tuple[float, float]:
        return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
