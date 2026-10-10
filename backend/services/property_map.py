"""
The live property map: where people are across all cameras, on the floorplan.

Each camera's tracker follows people in its own picture. Here the point where each of them stands
goes through that camera's calibration onto the map, is smoothed (an alpha-beta filter, a Kalman
filter with fixed gains) and gets a speed. Sightings of one person are then joined under one map id:

- Same moment: sightings from different cameras within MERGE_METRES of each other, seen within
  SAME_MOMENT seconds of each other, are one person.
- Hand-over: someone who appears where a person was lost a moment ago, no faster than WALK_SPEED
  could have taken them there, keeps that person's id. This also covers one camera losing and
  finding someone again.
- Faces win: once any camera recognises a person (an insider or a remembered visitor) the map
  person keeps that name, sightings recognised as two different people are never joined, and a
  face matching a person lost a moment ago picks up their id wherever they reappear.

Map ids last while someone is in view and HANDOFF_SECONDS after. Positions are kept in memory for
HISTORY_SECONDS for the replay; nothing is written to disk.
"""
import math
import threading
from collections import deque
from dataclasses import dataclass, field

from models.domain import Detection
from services.floorplan import Calibration, CalibrationError, calibrate, project
from services.settings_service import Settings, settings_service
from services.visitor_service import display_name

LIVE_SECONDS = 2.0       # a sighting counts this long after its camera last detected the person
SAME_MOMENT = 0.5        # sightings this close in time are compared as simultaneous
MERGE_METRES = 1.5       # sightings this close at the same moment are one person
SPLIT_METRES = 3.0       # one person's sightings this far apart at the same moment are two people
WALK_SPEED = 3.0         # m/s: the fastest walk a hand-over may imply
HANDOFF_SECONDS = 10.0   # how long a lost person's id can be picked up again
JUMP_METRES = 3.0        # a sighting that jumps further between two detections starts afresh
MAX_SPEED = 8.0          # m/s; anything faster is measurement noise
ALPHA, BETA = 0.5, 0.2   # how much each detection corrects the position and the velocity
PREDICT_SECONDS = 0.5    # positions are carried forward along the walking direction at most this long
DISPLAY_SECONDS = 0.3    # time constant of the shown position, so dots glide between detections
TRAIL_SECONDS = 30.0
TRAIL_STEP = 0.5
HISTORY_SECONDS = 30 * 60
HISTORY_STEP = 1.0
STEP_SECONDS = 0.1       # the map is brought up to date at most this often when asked for it


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _differ(a: tuple | None, b: tuple | None) -> bool:
    """Faces recognised as two different people."""
    return a is not None and b is not None and a != b


@dataclass
class Sighting:
    """One person in one camera's picture (one track of that camera's tracker), in map metres."""
    camera: str
    track: int
    x: float
    y: float
    seen: float                  # time of the detection the position comes from
    vx: float = 0.0
    vy: float = 0.0
    status: str = "pending"
    name: str | None = None      # insider
    visitor: int | None = None
    visitor_label: str | None = None
    person: int | None = None    # map id
    joined: float = 0.0          # when it joined that person

    @property
    def identity(self) -> tuple | None:
        if self.status == "known" and self.name:
            return ("insider", self.name)
        if self.visitor is not None:
            return ("visitor", self.visitor)
        return None

    @property
    def label(self) -> str:
        if self.status == "known" and self.name:
            return self.name
        if self.visitor is not None:
            return display_name(self.visitor, self.visitor_label)
        return "Unknown" if self.status == "unknown" else "Checking"

    def at(self, t: float) -> tuple[float, float]:
        dt = min(max(t - self.seen, 0.0), PREDICT_SECONDS)
        return self.x + self.vx * dt, self.y + self.vy * dt

    def update(self, x: float, y: float, now: float) -> None:
        dt = max(now - self.seen, 1e-3)
        px, py = self.x + self.vx * dt, self.y + self.vy * dt
        rx, ry = x - px, y - py
        if math.hypot(rx, ry) > JUMP_METRES:
            self.x, self.y, self.vx, self.vy = x, y, 0.0, 0.0
        else:
            self.x, self.y = px + ALPHA * rx, py + ALPHA * ry
            self.vx, self.vy = self.vx + BETA * rx / dt, self.vy + BETA * ry / dt
            speed = math.hypot(self.vx, self.vy)
            if speed > MAX_SPEED:
                self.vx, self.vy = self.vx * MAX_SPEED / speed, self.vy * MAX_SPEED / speed
        self.seen = max(self.seen, now)


@dataclass
class Person:
    id: int
    first: float
    seen: float = 0.0                         # the last time any camera saw them
    x: float = 0.0                            # shown position, metres
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    shown: float | None = None                # when x, y were last brought up to date
    last: tuple[float, float] = (0.0, 0.0)    # where a camera last saw them
    identity: tuple | None = None
    label: str = ""                           # the identity's name
    identified_on: str = ""                   # the camera that recognised them
    trail: deque = field(default_factory=deque)  # (time, x, y)


class PropertyMap:
    def __init__(self, settings=settings_service):
        self.settings = settings
        self._lock = threading.Lock()
        self._cfg: Settings | None = None
        self._map_key: tuple | None = None
        self._points: dict[str, list] = {}
        self._cals: dict[str, Calibration] = {}
        self._problems: dict[str, str] = {}
        self._sightings: dict[tuple[str, int], Sighting] = {}
        self._people: dict[int, Person] = {}
        self._next_id = 1
        self._stepped = 0.0
        self._sampled = 0.0
        self._history: deque = deque(maxlen=int(HISTORY_SECONDS / HISTORY_STEP) * 20)

    # ---- settings ------------------------------------------------------------------------
    def _reset(self) -> None:
        self._points.clear()
        self._cals.clear()
        self._problems.clear()
        self._sightings.clear()
        self._people.clear()
        self._history.clear()

    def _refresh(self) -> Settings:
        """Follows the settings: a new floorplan or scale starts the map afresh, and a camera's
        sightings are dropped when its calibration changes."""
        cfg = self.settings.get()
        if cfg is self._cfg:
            return cfg
        m = cfg.map
        key = (m.image, m.width, m.height, m.metres_per_px)
        if key != self._map_key:
            self._map_key = key
            self._reset()
        ready = bool(m.image and m.metres_per_px)
        cameras = {c.id: c for c in cfg.cameras}
        for cam_id in set(self._points) | set(cameras):
            cam = cameras.get(cam_id)
            pairs = cam.map_points if cam is not None and ready else []
            if cam is not None and self._points.get(cam_id) == pairs:
                continue
            self._cals.pop(cam_id, None)
            self._problems.pop(cam_id, None)
            for k in [k for k in self._sightings if k[0] == cam_id]:
                del self._sightings[k]
            if cam is None:  # removed
                del self._points[cam_id]
                continue
            self._points[cam_id] = pairs
            if pairs:
                try:
                    self._cals[cam_id] = calibrate(pairs, m.metres_per_px)
                except CalibrationError as exc:
                    self._problems[cam_id] = str(exc)
        self._cfg = cfg
        return cfg

    def calibrations(self) -> dict[str, tuple[Calibration | None, str | None]]:
        with self._lock:
            self._refresh()
            return {cam_id: (self._cals.get(cam_id), self._problems.get(cam_id)) for cam_id in self._points}

    # ---- input from the cameras -------------------------------------------------------------
    def observe(self, camera_id: str, detections: list[Detection], width: int, height: int, now: float) -> None:
        """Takes a camera's tracked people after each detection run."""
        with self._lock:
            cfg = self._refresh()
            cal = self._cals.get(camera_id)
            if cal is None:
                return
            mpp = cfg.map.metres_per_px
            for d in detections:
                if d.simulated or d.track_id is None:
                    continue  # a test intrusion is nobody standing anywhere
                x1, _, x2, y2 = d.bbox
                spot = project(cal, (x1 + x2) / 2 / width, min(y2 / height, 0.999))  # where they stand
                if spot is None:
                    continue
                x, y = spot[0] * mpp, spot[1] * mpp
                s = self._sightings.get((camera_id, d.track_id))
                if s is None:
                    s = self._sightings[(camera_id, d.track_id)] = Sighting(camera_id, d.track_id, x, y, now)
                else:
                    s.update(x, y, now)
                s.status, s.name, s.visitor, s.visitor_label = d.status, d.identity, d.visitor_id, d.visitor_label
            self._step(now)

    # ---- joining sightings into people ------------------------------------------------------
    def _members(self) -> dict[int, list[Sighting]]:
        members: dict[int, list[Sighting]] = {}
        for s in self._sightings.values():
            if s.person in self._people:
                members.setdefault(s.person, []).append(s)
            else:
                s.person = None
        return members

    @staticmethod
    def _apart(a: Sighting, b: Sighting) -> bool:
        """Two sightings that can't be the same person."""
        if _differ(a.identity, b.identity):
            return True
        if abs(a.seen - b.seen) > SAME_MOMENT:
            return False
        if a.camera == b.camera:
            return True  # one camera sees them as two people
        t = max(a.seen, b.seen)
        return _dist(a.at(t), b.at(t)) > SPLIT_METRES

    @staticmethod
    def _close(a: list[Sighting], b: list[Sighting]) -> bool:
        for s in a:
            for o in b:
                t = max(s.seen, o.seen)
                if abs(s.seen - o.seen) <= SAME_MOMENT and _dist(s.at(t), o.at(t)) <= MERGE_METRES:
                    return True
        return False

    def _match(self, s: Sighting, members: dict[int, list[Sighting]]) -> int | None:
        """The person a new sighting belongs to, if any."""
        best, best_score = None, math.inf
        for pid, p in self._people.items():
            ms = members.get(pid, [])
            if _differ(s.identity, p.identity) or any(self._apart(s, m) for m in ms if m.camera == s.camera
                                                      or _differ(s.identity, m.identity)):
                continue
            together = [m for m in ms if abs(m.seen - s.seen) <= SAME_MOMENT]
            if together:  # another camera sees them right now
                score = min(_dist(m.at(s.seen), (s.x, s.y)) for m in together)
                if score > MERGE_METRES:
                    continue
            else:  # lost a moment ago
                gap = s.seen - p.seen
                if gap > HANDOFF_SECONDS:
                    continue
                if s.identity is not None and s.identity == p.identity:
                    score = -1.0  # their face says so, wherever they reappear
                else:
                    score = _dist(p.last, (s.x, s.y))
                    if score > WALK_SPEED * max(gap, 0.0) + MERGE_METRES:
                        continue
            if score < best_score:
                best, best_score = pid, score
        return best

    def _join(self, s: Sighting, pid: int, members: dict[int, list[Sighting]], now: float) -> None:
        s.person, s.joined = pid, now
        ms = members.setdefault(pid, [])
        for old in [m for m in ms if m.camera == s.camera]:  # that camera lost and found them again
            ms.remove(old)
            self._sightings.pop((old.camera, old.track), None)
        ms.append(s)

    def _step(self, now: float) -> None:
        now = max(now, self._stepped)  # cameras report slightly out of order
        self._stepped = now
        for key in [k for k, s in self._sightings.items() if now - s.seen > LIVE_SECONDS]:
            del self._sightings[key]
        members = self._members()

        # Sightings of one person that turn out to be different people part; the newest leaves
        for pid, ms in members.items():
            ms.sort(key=lambda m: m.joined)
            kept: list[Sighting] = []
            for s in ms:
                if _differ(s.identity, self._people[pid].identity) or any(self._apart(s, k) for k in kept):
                    s.person = None
                else:
                    kept.append(s)
            ms[:] = kept

        for s in sorted((s for s in self._sightings.values() if s.person is None), key=lambda s: s.seen):
            pid = self._match(s, members)
            if pid is None:
                pid = self._next_id
                self._next_id += 1
                self._people[pid] = Person(pid, first=now)
            self._join(s, pid, members, now)

        for pid, ms in members.items():
            self._identify(self._people[pid], ms)

        # People found separately who turn out to be one: the older id stays
        ids = sorted(self._people)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if a not in self._people or b not in self._people:
                    continue
                pa, pb = self._people[a], self._people[b]
                ma, mb = members.get(a, []), members.get(b, [])
                if _differ(pa.identity, pb.identity) or any(self._apart(x, y) for x in ma for y in mb):
                    continue
                if not mb:
                    continue
                same_face = (not ma and pa.identity is not None and pa.identity == pb.identity
                             and pb.first >= pa.seen - SAME_MOMENT and now - pa.seen <= HANDOFF_SECONDS)
                if same_face or (ma and not {m.camera for m in ma} & {m.camera for m in mb} and self._close(ma, mb)):
                    for s in mb:
                        s.person, s.joined = a, now
                    members[a] = ma + mb
                    members.pop(b, None)
                    pa.identity, pa.label = pa.identity or pb.identity, pa.label or pb.label
                    pa.identified_on = pa.identified_on or pb.identified_on
                    del self._people[b]

        for pid, p in list(self._people.items()):
            ms = members.get(pid)
            if not ms:
                if now - p.seen > HANDOFF_SECONDS:
                    del self._people[pid]
                continue
            self._move(p, ms, now)
        if now - self._sampled >= HISTORY_STEP:
            self._sampled = now
            self._record(now)

    @staticmethod
    def _identify(p: Person, ms: list[Sighting]) -> None:
        recognised = sorted((m for m in ms if m.identity), key=lambda m: m.identity[0] != "insider")
        if recognised:
            if recognised[0].identity != p.identity:
                p.identity, p.identified_on = recognised[0].identity, recognised[0].camera
            p.label = recognised[0].label  # a visitor may have been named meanwhile

    @staticmethod
    def _move(p: Person, ms: list[Sighting], now: float) -> None:
        n = len(ms)
        p.seen = max(p.seen, max(m.seen for m in ms))
        p.last = (sum(m.x for m in ms) / n, sum(m.y for m in ms) / n)
        tx, ty = (sum(m.at(now)[i] for m in ms) / n for i in (0, 1))
        if p.shown is None:
            p.x, p.y = tx, ty
        else:
            k = 1 - math.exp(-max(now - p.shown, 0.0) / DISPLAY_SECONDS)
            p.x, p.y = p.x + k * (tx - p.x), p.y + k * (ty - p.y)
        p.shown = now
        p.vx, p.vy = sum(m.vx for m in ms) / n, sum(m.vy for m in ms) / n
        if not p.trail or now - p.trail[-1][0] >= TRAIL_STEP:
            p.trail.append((now, p.x, p.y))
        while p.trail and now - p.trail[0][0] > TRAIL_SECONDS:
            p.trail.popleft()

    @staticmethod
    def _describe(p: Person, ms: list[Sighting]) -> tuple[str, str]:
        """Status and label: a recognised face wins over what other cameras make of them."""
        if p.identity and p.identity[0] == "insider":
            return "known", p.label
        if p.identity:
            return "unknown", p.label
        if any(m.status == "unknown" for m in ms):
            return "unknown", "Unknown"
        return "pending", "Checking"

    def _record(self, now: float) -> None:
        mpp = self._cfg.map.metres_per_px if self._cfg else None
        if not mpp:
            return
        for pid, ms in self._members().items():
            p = self._people[pid]
            status, label = self._describe(p, ms)
            self._history.append((round(now, 1), pid, round(p.x / mpp, 1), round(p.y / mpp, 1), status, label))

    # ---- output ----------------------------------------------------------------------------
    def snapshot(self, now: float) -> dict:
        """The people on the map now and the cameras' ground areas, positions in map pixels."""
        with self._lock:
            cfg = self._refresh()
            if now - self._stepped >= STEP_SECONDS:
                self._step(now)
            mpp = cfg.map.metres_per_px or 1.0
            names = {c.id: c.name for c in cfg.cameras}
            people = []
            for pid, ms in sorted(self._members().items()):
                p = self._people[pid]
                status, label = self._describe(p, ms)
                people.append({
                    "id": pid, "x": round(p.x / mpp, 1), "y": round(p.y / mpp, 1),
                    "speed": round(math.hypot(p.vx, p.vy), 1), "label": label, "status": status,
                    "cameras": sorted({names.get(m.camera, m.camera) for m in ms}),
                    "views": [{"camera": names.get(m.camera, m.camera), "status": m.status, "label": m.label}
                              for m in sorted(ms, key=lambda m: names.get(m.camera, m.camera))],
                    "identified_on": names.get(p.identified_on, p.identified_on) or None,
                    "since": round(p.first, 1),
                    "trail": [[round(x / mpp, 1), round(y / mpp, 1), round(t, 1)] for t, x, y in p.trail],
                })
            cameras = []
            for c in cfg.cameras:
                if c.enabled and c.id in self._cals:
                    cal = self._cals[c.id].to_dict()
                    cameras.append({"id": c.id, "name": c.name, "field": cal["field"], "at": cal["at"]})
            return {"type": "map", "time": round(now, 2), "people": people, "cameras": cameras}

    def history(self, minutes: float, now: float) -> list[tuple]:
        """Samples (time, id, x, y, status, label) from the last `minutes`, oldest first."""
        with self._lock:
            self._refresh()
            since = now - minutes * 60
            return [s for s in self._history if s[0] >= since]


property_map = PropertyMap()
