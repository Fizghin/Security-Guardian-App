"""
Follows people across detection runs so identity decisions are made from
several looks at a face instead of a single frame.

Each track collects votes: a face matching an insider adds to that insider, a
clear face that matches nobody counts against all of them. A track becomes
"known" once an insider has two votes and more votes than "unknown"; it becomes
"unknown" (a stranger) when its face clearly matched nobody twice, or when it
has stayed unidentified for `identify_seconds`. Until then it is "pending",
which lets a resident walking towards the camera be recognised before the
system starts talking to them.
"""
from dataclasses import dataclass, field

import numpy as np

from models.domain import Detection

STRONG_MARGIN = 0.12  # score this far above the threshold counts double
MAX_AGE = 2.5         # seconds a track survives without being detected


@dataclass
class FaceEvidence:
    face_visible: bool = False
    quality_ok: bool = False
    name: str | None = None
    score: float = 0.0
    # Only when asked for (remembering visitors), for faces that pass the quality gate:
    feature: np.ndarray | None = None  # the face embedding
    crop: np.ndarray | None = None     # the face with some margin; a view into the frame
    quality: float = 0.0               # how good a look this is, to keep the best one


@dataclass
class Track:
    id: int
    bbox: list[float]
    first_seen: float
    last_seen: float
    votes: dict[str, float] = field(default_factory=dict)
    unknown_votes: int = 0
    identity: str | None = None
    best_score: float = 0.0
    face_seen_at: float = 0.0
    # For telling still things from people (learning_service): where the track began, the furthest its
    # box centre has been from there (x, y in pixels), the largest change in its width or height (a ratio),
    # and whether a face was ever seen on it.
    first_bbox: list[float] = field(default_factory=list)
    moved: tuple[float, float] = (0.0, 0.0)
    resized: float = 1.0
    faced: bool = False


def iou(a: list[float], b: list[float]) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _near(a: list[float], b: list[float]) -> bool:
    """Fallback match for people who moved a lot between two detection runs."""
    aw, ah, bw, bh = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]
    if not (0.5 < (bw * bh) / max(1.0, aw * ah) < 2.0):
        return False
    dx = (a[0] + a[2] - b[0] - b[2]) / 2
    dy = (a[1] + a[3] - b[1] - b[3]) / 2
    return (dx * dx + dy * dy) ** 0.5 < 0.6 * max(aw, bw)


def _note_movement(track: Track, box: list[float]) -> None:
    """How far a track has moved and changed size since it began; identity is not affected."""
    first = track.first_bbox
    dx = abs(box[0] + box[2] - first[0] - first[2]) / 2
    dy = abs(box[1] + box[3] - first[1] - first[3]) / 2
    track.moved = (max(track.moved[0], dx), max(track.moved[1], dy))
    for i in (0, 1):  # width, then height
        ratio = max(1.0, box[i + 2] - box[i]) / max(1.0, first[i + 2] - first[i])
        track.resized = max(track.resized, ratio, 1 / ratio)


class Tracker:
    def __init__(self):
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    def reset(self) -> None:
        self.tracks.clear()

    def _assign(self, detections: list[Detection]) -> list[Track | None]:
        pairs = sorted(((iou(d.bbox, t.bbox), i, tid) for i, d in enumerate(detections)
                        for tid, t in self.tracks.items()), reverse=True)
        assigned: list[Track | None] = [None] * len(detections)
        used: set[int] = set()
        for score, i, tid in pairs:
            if score < 0.2:
                break
            if assigned[i] is None and tid not in used:
                assigned[i] = self.tracks[tid]
                used.add(tid)
        for i, d in enumerate(detections):
            if assigned[i] is not None:
                continue
            for tid, t in self.tracks.items():
                if tid not in used and _near(d.bbox, t.bbox):
                    assigned[i] = t
                    used.add(tid)
                    break
        return assigned

    def update(self, detections: list[Detection], evidence: list[FaceEvidence], now: float, threshold: float,
               identify_seconds: float, recognition_active: bool) -> None:
        """Annotates detections in place with track id, identity and status."""
        for tid in [tid for tid, t in self.tracks.items() if now - t.last_seen > MAX_AGE]:
            del self.tracks[tid]

        assigned = self._assign(detections)
        for det, ev, track in zip(detections, evidence, assigned):
            if track is None:
                track = Track(self._next_id, det.bbox, now, now, first_bbox=list(det.bbox))
                self.tracks[track.id] = track
                self._next_id += 1
            track.bbox, track.last_seen = det.bbox, now
            _note_movement(track, det.bbox)

            if ev.face_visible:
                track.face_seen_at = now
                track.faced = True
            if ev.name:
                weight = 2.0 if ev.score >= threshold + STRONG_MARGIN else 1.0
                if not ev.quality_ok:
                    weight = 0.5  # a match on a poor-quality face is weak evidence
                track.votes[ev.name] = track.votes.get(ev.name, 0.0) + weight
                track.best_score = max(track.best_score, ev.score)
            elif ev.quality_ok:
                track.unknown_votes += 1

            if track.votes:
                name, votes = max(track.votes.items(), key=lambda kv: kv[1])
                if votes >= 2 and votes > track.unknown_votes:
                    track.identity = name
                elif track.identity and track.unknown_votes >= track.votes.get(track.identity, 0) + 3:
                    track.identity = None  # a clear look showed someone else

            det.track_id = track.id
            det.face_visible = now - track.face_seen_at < 3.0
            det.match_score = round(track.best_score, 2) if track.votes else None
            if track.identity:
                det.known, det.identity, det.status = True, track.identity, "known"
            elif det.simulated or not recognition_active or track.unknown_votes >= 2 \
                    or now - track.first_seen >= identify_seconds:
                det.known, det.identity, det.status = False, None, "unknown"
            else:
                det.known, det.identity, det.status = False, None, "pending"
