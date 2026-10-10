"""
Watch spots: named areas of a camera's picture, such as a gate, a door or a window,
that should look the way they usually do.

Each spot keeps a reference: a small, blurred grey picture of it that slowly
follows gradual changes (dusk, shadows) while the spot is stable. A spot has
changed when a large share of it differs from the reference, more than the rest
of the picture does, for 5 s or more with nobody inside it. Brightness changes
across the whole picture are evened out by comparing against the rest of the
frame; when the whole picture changes (lights, night vision, the camera moved)
nothing is judged, and the references are taken again once it settles.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

WORK_WIDTH = 320         # spots are compared on a small copy of the picture
BEFORE_WIDTH = 480       # size of the "before" picture kept for each spot
CHECK_EVERY = 0.5
PIXEL_DIFF = 25          # grey levels: a pixel this different from the reference has changed
CHANGED_SHARE = 0.3      # share of a spot that must differ (beyond the rest of the picture)
NORMAL_SHARE = 0.15      # a changed spot is back to normal below this
LEARN_BELOW = 0.1        # the reference keeps learning while less than this differs
LEARN_RATE = 0.03
SETTLE_SECONDS = 5.0     # changed, or back to normal, for this long before it counts
PERSON_HOLD = 2.5        # a person seen in a spot this recently still counts as inside it
WHOLE_SHARE = 0.3        # the rest of the picture changed this much: the whole picture changed
RETAKE_SECONDS = 10.0    # after a whole-picture change has lasted this long, references are taken again


@dataclass
class Spot:
    name: str
    polygon: list[list[float]]
    mask: np.ndarray            # the spot's pixels within rect, on the small picture
    rect: tuple[int, int, int, int]
    ref: np.ndarray | None = None
    share: float = 0.0          # how much of it differs now
    different_since: float | None = None
    clear_since: float | None = None   # different, with nobody inside, since
    normal_since: float | None = None  # changed, and matching the reference again, since
    changed: bool = False
    changed_since: float | None = None  # when the change began
    person_at: float = float("-inf")
    before: tuple[float, np.ndarray] | None = None  # (time, colour picture) when last stable
    before_change: tuple[float, np.ndarray] | None = field(default=None, repr=False)


@dataclass
class Happening:
    kind: str  # "changed" | "normal"
    spot: Spot
    since: float = 0.0


def _rect_mask(polygon: list[list[float]], size: tuple[int, int]) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    w, h = size
    points = np.array([[round(x * (w - 1)), round(y * (h - 1))] for x, y in polygon], dtype=np.int32)
    full = np.zeros((h, w), np.uint8)
    cv2.fillPoly(full, [points], 1)
    x, y, rw, rh = cv2.boundingRect(points)
    return full[y:y + rh, x:x + rw].astype(bool), (x, y, rw, rh)


class SpotWatch:
    def __init__(self, spots: list[tuple[str, list[list[float]]]]):
        self.config = [(name, [list(p) for p in polygon]) for name, polygon in spots]
        self.spots: list[Spot] = []
        self._size: tuple[int, int] | None = None
        self._rest: np.ndarray | None = None  # pixels outside every spot
        self._rest_ref: np.ndarray | None = None
        self._last_check = float("-inf")
        self._unsettled_since: float | None = None

    def _setup(self, size: tuple[int, int]) -> None:
        self._size = size
        w, h = size
        self.spots = []
        outside = np.ones((h, w), bool)
        for name, polygon in self.config:
            mask, rect = _rect_mask(polygon, size)
            x, y, rw, rh = rect
            outside[y:y + rh, x:x + rw] &= ~mask
            self.spots.append(Spot(name, polygon, mask, rect))
        # A spot's surroundings move with it (a gate's shadow), so they don't count as the rest of the picture.
        self._rest = cv2.erode(outside.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
        self._rest_ref = None

    @staticmethod
    def _small(frame) -> np.ndarray:
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (WORK_WIDTH, max(1, round(WORK_WIDTH * h / w))), interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float32)

    def _patch(self, grey: np.ndarray, spot: Spot) -> np.ndarray:
        x, y, w, h = spot.rect
        return grey[y:y + h, x:x + w]

    def _retake(self, grey: np.ndarray) -> None:
        self._rest_ref = grey.copy()
        for spot in self.spots:
            spot.ref = self._patch(grey, spot).copy()
            spot.different_since = spot.clear_since = None

    def _occupied(self, spot: Spot, people: list[list[float]], scale: float) -> bool:
        x, y, w, h = spot.rect
        total = spot.mask.sum()
        for x1, y1, x2, y2 in people:
            px1, py1 = max(0, int(x1 * scale) - x), max(0, int(y1 * scale) - y)
            px2, py2 = min(w, int(x2 * scale) + 1 - x), min(h, int(y2 * scale) + 1 - y)
            if px2 > px1 and py2 > py1 and spot.mask[py1:py2, px1:px2].sum() >= 0.05 * total:
                return True
        return False

    def update(self, frame, people: list[list[float]], now: float) -> list["Happening"]:
        """frame: the camera's picture; people: boxes of everyone in view, in frame pixels."""
        if not self.config or now - self._last_check < CHECK_EVERY:
            return []
        self._last_check = now
        grey = self._small(frame)
        size = (grey.shape[1], grey.shape[0])
        if size != self._size:
            self._setup(size)  # first picture, or it changed shape
        if self._rest_ref is None:
            self._retake(grey)
            return []

        # How the rest of the picture's brightness and contrast changed; the spots are expected to follow it.
        gain, offset, rest_share = 1.0, 0.0, 0.0
        rest = self._rest
        if rest.any():
            now_rest, ref_rest = grey[rest], self._rest_ref[rest]
            gain = (float(now_rest.std()) + 1) / (float(ref_rest.std()) + 1)
            offset = float(now_rest.mean()) - gain * float(ref_rest.mean())
            rest_share = float((np.abs(now_rest - (gain * ref_rest + offset)) > PIXEL_DIFF).mean())
        if rest_share >= WHOLE_SHARE:
            if self._unsettled_since is None:
                self._unsettled_since = now
            elif now - self._unsettled_since >= RETAKE_SECONDS:
                self._unsettled_since = None
                self._retake(grey)
            return []  # nothing can be told about a spot while the whole picture changes
        self._unsettled_since = None
        self._rest_ref += LEARN_RATE * (grey - self._rest_ref)

        scale = WORK_WIDTH / frame.shape[1]
        colour = None
        out: list[Happening] = []
        for spot in self.spots:
            patch = self._patch(grey, spot)
            if spot.ref is None or not spot.mask.any():  # just accepted as the new normal
                spot.ref = patch.copy()
                continue
            expected = gain * spot.ref + offset
            spot.share = max(0.0, float((np.abs(patch - expected)[spot.mask] > PIXEL_DIFF).mean()) - rest_share)
            if self._occupied(spot, people, scale):
                spot.person_at = now
            person = now - spot.person_at <= PERSON_HOLD
            if spot.changed:
                if spot.share < NORMAL_SHARE and not person:
                    spot.normal_since = spot.normal_since or now
                    if now - spot.normal_since >= SETTLE_SECONDS:
                        spot.changed, spot.normal_since, spot.changed_since = False, None, None
                        out.append(Happening("normal", spot))
                else:
                    spot.normal_since = None
                continue
            if spot.share >= CHANGED_SHARE:
                spot.different_since = spot.different_since or now
                spot.clear_since = None if person else (spot.clear_since or now)
                if spot.clear_since is not None and now - spot.clear_since >= SETTLE_SECONDS:
                    spot.changed, spot.changed_since, spot.normal_since = True, spot.different_since, None
                    spot.before_change = spot.before
                    out.append(Happening("changed", spot, spot.different_since))
                continue
            if spot.share < NORMAL_SHARE:
                spot.different_since = spot.clear_since = None
            if spot.share < LEARN_BELOW and not person:
                spot.ref += LEARN_RATE * (patch - spot.ref)
                if colour is None:
                    h, w = frame.shape[:2]
                    colour = cv2.resize(frame, (BEFORE_WIDTH, max(1, round(BEFORE_WIDTH * h / w))),
                                        interpolation=cv2.INTER_AREA)
                spot.before = (now, colour)
        return out

    def accept(self) -> list[str]:
        """Take the changed spots as they look now as their new normal. Returns their names."""
        names = []
        for spot in self.spots:
            if spot.changed:
                spot.ref, spot.changed, spot.changed_since = None, False, None
                spot.different_since = spot.clear_since = spot.normal_since = None
                names.append(spot.name)
        return names
