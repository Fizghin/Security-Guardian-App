"""
Where people walk: spatial activity heatmaps and live motion trails.

Heatmaps: every detection run adds each person's foot point (the bottom middle of
their box, relative to the picture) to a coarse per-camera grid. Over days this
shows the paths people actually take through a camera's view, which tells you
where zones should go and which corners the camera never sees anyone reach.
A person standing still adds at most one sample per second, so one loiterer
doesn't drown out everything else.

Trails: the last few seconds of each tracked person's foot points, drawn on the
live picture so you can see where someone came from.
"""
import json
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from config import DATA_DIR
from models.domain import Detection

COLS, ROWS = 48, 27
SAVE_SECONDS = 60
SAMPLE_SECONDS = 1.0  # per tracked person
TRAIL_SECONDS = 6.0
TRAIL_POINTS = 40


def foot_point(d: Detection, w: int, h: int) -> tuple[float, float]:
    x1, _, x2, y2 = d.bbox
    return min(1.0, max(0.0, (x1 + x2) / 2 / w)), min(1.0, max(0.0, y2 / h))


class HeatmapStore:
    def __init__(self, path: Path = DATA_DIR / "heatmaps.json"):
        self.path = path
        self._lock = threading.Lock()
        self._grids: dict[str, dict] = {}
        self._last_sample: dict[tuple[str, int], float] = {}
        self._dirty = False
        self._saved_at = time.monotonic()
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for cam, g in data.items():
                grid = np.array(g["grid"], dtype=np.float32)
                if grid.shape == (ROWS, COLS):
                    self._grids[cam] = {"grid": grid, "samples": int(g.get("samples", 0)),
                                        "since": g.get("since") or time.time(), "strangers": int(g.get("strangers", 0))}
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _entry(self, camera_id: str) -> dict:
        if camera_id not in self._grids:
            self._grids[camera_id] = {"grid": np.zeros((ROWS, COLS), np.float32), "samples": 0, "since": time.time(),
                                      "strangers": 0}
        return self._grids[camera_id]

    def add(self, camera_id: str, detections: list[Detection], w: int, h: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not detections or w <= 0 or h <= 0:
            return
        with self._lock:
            entry = self._entry(camera_id)
            for d in detections:
                if d.simulated:
                    continue
                key = (camera_id, d.track_id if d.track_id is not None else -1)
                if d.track_id is not None and now - self._last_sample.get(key, 0.0) < SAMPLE_SECONDS:
                    continue
                self._last_sample[key] = now
                fx, fy = foot_point(d, w, h)
                col, row = min(COLS - 1, int(fx * COLS)), min(ROWS - 1, int(fy * ROWS))
                entry["grid"][row, col] += 1.0
                entry["samples"] += 1
                if d.status == "unknown":
                    entry["strangers"] += 1
                self._dirty = True
            if len(self._last_sample) > 2000:  # forget tracks that are long gone
                cutoff = now - 60
                self._last_sample = {k: t for k, t in self._last_sample.items() if t > cutoff}
        if time.monotonic() - self._saved_at > SAVE_SECONDS:
            self.save()

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data = {cam: {"grid": np.round(g["grid"], 2).tolist(), "samples": g["samples"], "since": g["since"],
                          "strangers": g["strangers"]} for cam, g in self._grids.items()}
            self._dirty = False
            self._saved_at = time.monotonic()
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            print(f"[heatmap] Could not save: {exc}")

    def get(self, camera_id: str) -> dict:
        with self._lock:
            g = self._grids.get(camera_id)
            if g is None:
                return {"cols": COLS, "rows": ROWS, "grid": [], "max": 0, "samples": 0, "since": None, "strangers": 0,
                        "hotspots": []}
            grid = g["grid"].copy()
            samples, since, strangers = g["samples"], g["since"], g["strangers"]
        hot = []
        if grid.max() > 0:
            # The three busiest cells, as places on the picture
            for idx in np.argsort(grid, axis=None)[::-1][:3]:
                r, c = divmod(int(idx), COLS)
                if grid[r, c] <= 0:
                    break
                hot.append({"x": (c + 0.5) / COLS, "y": (r + 0.5) / ROWS, "share": float(grid[r, c] / grid.sum()),
                            "where": _describe(c, r)})
        return {"cols": COLS, "rows": ROWS, "grid": np.round(grid, 2).tolist(), "max": float(grid.max()),
                "samples": samples, "since": since, "strangers": strangers, "hotspots": hot}

    def reset(self, camera_id: str) -> None:
        with self._lock:
            self._grids.pop(camera_id, None)
            self._dirty = True
        self.save()


def _describe(col: int, row: int) -> str:
    v = "top" if row < ROWS / 3 else "bottom" if row >= 2 * ROWS / 3 else "middle"
    h = "left" if col < COLS / 3 else "right" if col >= 2 * COLS / 3 else "centre"
    return "centre" if (v, h) == ("middle", "centre") else f"{v} {h}"


class Trails:
    """Recent foot points per tracked person on one camera, for drawing on the live picture."""

    def __init__(self):
        self._points: dict[int, deque] = {}
        self._status: dict[int, str] = {}

    def update(self, detections: list[Detection], w: int, h: int, now: float) -> None:
        for d in detections:
            if d.track_id is None:
                continue
            pts = self._points.setdefault(d.track_id, deque(maxlen=TRAIL_POINTS))
            pts.append((now, *foot_point(d, w, h)))
            self._status[d.track_id] = "test" if d.simulated else d.status
        for tid in [t for t, pts in self._points.items() if not pts or now - pts[-1][0] > TRAIL_SECONDS]:
            self._points.pop(tid, None)
            self._status.pop(tid, None)

    def lines(self, now: float) -> list[tuple[str, list[tuple[float, float]]]]:
        out = []
        for tid, pts in list(self._points.items()):
            recent = [(x, y) for t, x, y in pts if now - t <= TRAIL_SECONDS]
            if len(recent) >= 2:
                out.append((self._status.get(tid, "unknown"), recent))
        return out


heatmap_store = HeatmapStore()
