"""
The property map's floorplan, and each camera's calibration onto it.

The owner uploads a picture of the property seen from above (or uses a blank grid), sets its
scale, and for each camera pairs 4 or more spots on the ground in the camera's picture with the
same spots on the map. A homography (a 3x3 perspective transform) then turns the point where
someone stands in the picture into their place on the map. It only holds for the flat ground the
pairs were picked on: a person on stairs or a raised deck lands in the wrong place.

Picture points are stored in 0..1 (like zones), map points in pixels of the floorplan picture.
"""
import math
import os
import time
from dataclasses import dataclass

import cv2
import numpy as np

from config import DATA_DIR

MAP_DIR = DATA_DIR / "map"  # the uploaded floorplan
MAX_PICTURE_BYTES = 10 * 1024 * 1024
MAX_PICTURE_SIDE = 8000
MIN_PICTURE_SIDE = 100
GRID_METRES_PER_PX = 0.05  # a blank grid is drawn at 20 pixels per metre
MIN_PAIRS, MAX_PAIRS = 4, 16
OUTLIER_METRES = 1.0     # a pair this far from where the others put it is left out of the fit
MIN_APART_PICTURE = 0.01  # share of the picture
MIN_APART_METRES = 0.25
# The ground area shown for a camera reaches about twice as far as its farthest calibration point
# (beyond that the calibration is a guess). Positions further away are not put on the map.
FAR_LIMIT = 0.5


class CalibrationError(ValueError):
    pass


@dataclass
class Calibration:
    matrix: np.ndarray            # picture (0..1) -> map pixels
    w_limit: float                # nearer than this (in homogeneous w) counts as within the ground area
    errors: list[float]           # per pair, metres between where the fit puts it and where it was marked
    error: float                  # root mean square over the pairs used, metres
    outliers: list[int]           # pairs (0-based) left out because they don't fit the others
    exact: bool                   # only 4 pairs were used, which always fit exactly: the error says nothing
    ground: list[list[float]]     # the ground area in view, on the map (map pixels)
    at: list[float]               # the nearest ground in view (bottom centre of the picture), on the map

    def to_dict(self) -> dict:
        return {"error": round(self.error, 2), "errors": [round(e, 2) for e in self.errors],
                "outliers": self.outliers, "exact": self.exact,
                "field": [[round(x, 1), round(y, 1)] for x, y in self.ground], "at": [round(v, 1) for v in self.at]}


def clean_pairs(pairs: list[list[float]]) -> list[list[float]]:
    """The pairs rounded for storage. Raises ValueError for pairs that can't be points."""
    if pairs and not MIN_PAIRS <= len(pairs) <= MAX_PAIRS:
        raise ValueError(f"A camera on the map needs {MIN_PAIRS} to {MAX_PAIRS} pairs of points")
    out = []
    for pair in pairs:
        if len(pair) != 4 or not all(math.isfinite(v) for v in pair):
            raise ValueError("Each pair is [picture x, picture y, map x, map y]")
        px, py, mx, my = pair
        if not (0 <= px <= 1 and 0 <= py <= 1):
            raise ValueError("Points on the camera picture must lie inside it")
        if not (0 <= mx <= MAX_PICTURE_SIDE and 0 <= my <= MAX_PICTURE_SIDE):
            raise ValueError("Points on the map must lie inside it")
        out.append([round(px, 4), round(py, 4), round(mx, 1), round(my, 1)])
    return out


def _check_spread(points: np.ndarray, min_apart: float, where: str) -> None:
    """Points that coincide or lie on one line can't define a perspective."""
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            if np.hypot(*(points[i] - points[j])) < min_apart:
                raise CalibrationError(f"Points {i + 1} and {j + 1} are in the same place on the {where}")
    spread = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
    if spread[1] < 0.05 * spread[0]:
        raise CalibrationError(f"The points on the {where} lie almost on one line. Spread them over the ground, "
                               "for example near the corners of the area the camera sees.")
    if n == 4:  # with 4 pairs every point counts: no three may be in a line
        size = max(np.hypot(*(a - b)) for a in points for b in points) ** 2
        for skip in range(4):
            a, b, c = (points[k] for k in range(4) if k != skip)
            area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
            if area < 0.01 * size:
                names = ", ".join(str(k + 1) for k in range(4) if k != skip)
                raise CalibrationError(f"Points {names} on the {where} lie almost on one line. Move one of them, "
                                       "or add a fifth pair.")


def _w(matrix: np.ndarray, x, y):
    return matrix[2, 0] * x + matrix[2, 1] * y + matrix[2, 2]


def _clip(polygon: list[tuple[float, float]], matrix: np.ndarray, limit: float) -> list[tuple[float, float]]:
    """The part of a picture polygon nearer than `limit` (one Sutherland-Hodgman pass)."""
    out = []
    for i, p in enumerate(polygon):
        q = polygon[(i + 1) % len(polygon)]
        wp, wq = _w(matrix, *p) - limit, _w(matrix, *q) - limit
        if wp >= 0:
            out.append(p)
        if (wp >= 0) != (wq >= 0):
            t = wp / (wp - wq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _project(m: np.ndarray, w_limit: float, x: float, y: float) -> tuple[float, float] | None:
    w = _w(m, x, y)
    if w < w_limit:
        return None
    return (m[0, 0] * x + m[0, 1] * y + m[0, 2]) / w, (m[1, 0] * x + m[1, 1] * y + m[1, 2]) / w


def project(cal: Calibration, x: float, y: float) -> tuple[float, float] | None:
    """Map pixels of a picture point (0..1) on the ground, or None beyond the camera's ground area."""
    return _project(cal.matrix, cal.w_limit, x, y)


def calibrate(pairs: list[list[float]], metres_per_px: float) -> Calibration:
    """The camera's perspective onto the map from pairs of [picture x, picture y, map x, map y].
    Raises CalibrationError, with what to fix, for pairs that can't come from a camera looking at
    flat ground."""
    if not MIN_PAIRS <= len(pairs) <= MAX_PAIRS:
        raise CalibrationError(f"Pick {MIN_PAIRS} to {MAX_PAIRS} pairs of points")
    src = np.array([p[:2] for p in pairs], np.float64)
    dst = np.array([p[2:] for p in pairs], np.float64)
    _check_spread(src, MIN_APART_PICTURE, "camera picture")
    _check_spread(dst * metres_per_px, MIN_APART_METRES, "map")

    method = cv2.RANSAC if len(pairs) > MIN_PAIRS else 0
    matrix, mask = cv2.findHomography(src, dst, method, OUTLIER_METRES / metres_per_px)
    unfit = CalibrationError("These pairs don't fit one flat ground. Check that each pair marks the same spot "
                             "in both pictures.")
    if matrix is None or mask is None or not np.all(np.isfinite(matrix)):
        raise unfit
    used = mask.ravel().astype(bool)
    if used.sum() < MIN_PAIRS:
        raise unfit
    if len(pairs) > MIN_PAIRS:
        _check_spread(src[used], MIN_APART_PICTURE, "camera picture")

    # The homography is only defined up to scale; pick the sign that makes the ground in view positive
    w = _w(matrix, src[:, 0], src[:, 1])
    if np.all(w[used] < 0):
        matrix, w = -matrix, -w
    if not np.all(w[used] > 0):
        raise unfit  # some points would be beyond the horizon of others
    if np.linalg.det(matrix) <= 0:
        raise CalibrationError("These pairs would show the camera's picture mirror-image on the map, which a camera "
                               "looking at the ground can't do. Check that each pair marks the same spot in both "
                               "pictures (for example, left and right swapped).")

    w_limit = FAR_LIMIT * float(w[used].min())
    fitted = cv2.perspectiveTransform(src.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    errors = (np.hypot(*(fitted - dst).T) * metres_per_px).tolist()
    used_errors = [e for e, u in zip(errors, used) if u]
    # Points exactly on the limit project fine; the clip only puts them there
    corners = _clip([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], matrix, w_limit)
    ground = [list(_project(matrix, w_limit * 0.999, x, y)) for x, y in corners]
    near = _project(matrix, w_limit, 0.5, 1.0)
    at = list(near) if near else np.mean(ground, axis=0).tolist()
    return Calibration(matrix=matrix, w_limit=w_limit, errors=errors,
                       error=float(np.sqrt(np.mean(np.square(used_errors)))),
                       outliers=[i for i, u in enumerate(used) if not u], exact=int(used.sum()) == MIN_PAIRS,
                       ground=ground, at=at)


# ---- the floorplan picture -------------------------------------------------------------------
def _picture_size(data: bytes) -> tuple[str, int, int] | None:
    """Format and size from a PNG or JPEG header, read before decoding so a picture claiming to be
    enormous is refused without allocating it."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return "png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:3] != b"\xff\xd8\xff":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # padding
            i += 1
            continue
        if 0xD0 <= marker <= 0xD9 or marker == 0x01:  # no length
            i += 2
            continue
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # start of frame
            return "jpg", int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def save_picture(data: bytes) -> tuple[str, int, int]:
    """Stores an uploaded floorplan in MAP_DIR and returns (file name, width, height). The picture is
    re-encoded, which drops metadata such as where a photo was taken. Raises ValueError."""
    if len(data) > MAX_PICTURE_BYTES:
        raise ValueError("The picture is larger than 10 MB")
    header = _picture_size(data)
    if header is None:
        raise ValueError("Upload a PNG or JPEG picture")
    kind, w, h = header
    if max(w, h) > MAX_PICTURE_SIDE:
        raise ValueError(f"The picture is too large ({w} × {h}); at most {MAX_PICTURE_SIDE} pixels a side")
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)  # also turns photos upright
    if image is None:
        raise ValueError("This picture could not be read")
    h, w = image.shape[:2]
    if min(w, h) < MIN_PICTURE_SIDE:
        raise ValueError(f"The picture is too small ({w} × {h}); at least {MIN_PICTURE_SIDE} pixels a side")
    ok, buf = cv2.imencode(f".{kind}", image, [cv2.IMWRITE_JPEG_QUALITY, 92] if kind == "jpg" else [])
    if not ok:
        raise ValueError("This picture could not be read")
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"floorplan-{int(time.time() * 1000)}.{kind}"
    tmp = MAP_DIR / f"{name}.tmp"
    tmp.write_bytes(buf.tobytes())
    os.replace(tmp, MAP_DIR / name)
    return name, w, h


def remove_pictures(keep: str = "") -> None:
    """Deletes floorplans other than `keep` (replaced or removed ones)."""
    if not MAP_DIR.is_dir():
        return
    for path in MAP_DIR.glob("floorplan-*"):
        if path.name != keep:
            try:
                path.unlink()
            except OSError as exc:
                print(f"[map] Could not delete {path.name}: {exc}")
