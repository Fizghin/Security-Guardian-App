"""
Activity heatmaps: where people spent time in each camera's picture.

Each person detection adds its foot point (the bottom centre of its box, where the person stands)
to a coarse grid over the picture: COLS columns and as many rows as the picture's shape gives, one
grid per camera per hour. A tracked person adds a point at most every SAMPLE_SECONDS, so the map
shows time spent rather than how often the detector ran.

Grids live in memory (adding a point is a dictionary update, so a camera's loop never waits on the
disk), are saved to storage/heatmaps/<camera>.json every few minutes and when the camera stops, and
hours older than KEEP_DAYS are dropped by the hourly clean-up.
"""
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from config import DATA_DIR
from models.domain import Detection

HEATMAPS_DIR = DATA_DIR / "heatmaps"
COLS = 64
SAMPLE_SECONDS = 0.5  # at most two points a second per tracked person
SAVE_SECONDS = 300
PRUNE_SECONDS = 3600
KEEP_DAYS = 30
HOUR = 3600
TRACK_MEMORY = 60  # throttling state of people not seen for this long is dropped
ZONE = (230, 230, 160)  # the live picture's zone outline colour
RENDER_WIDTH = 960
_CAMERA_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

Bucket = dict  # {"rows": int, "cols": int, "cells": {cell index: points}}


def grid_rows(width: int, height: int) -> int:
    return max(1, min(COLS, round(COLS * height / max(1, width))))


class HeatmapService:
    def __init__(self, directory: Path = HEATMAPS_DIR, clock=time.time):
        self.directory = directory
        self.clock = clock
        self._lock = threading.Lock()
        self._maps: dict[str, dict[int, Bucket]] = {}  # camera id -> hour start (epoch seconds) -> grid
        self._dirty: set[str] = set()
        self._sampled: dict[tuple[str, int], float] = {}  # (camera, track) -> when it last added a point
        self._saved_at = self._pruned_at = clock()

    def _path(self, camera_id: str) -> Path:
        if not _CAMERA_ID.match(camera_id):
            raise KeyError(camera_id)
        return self.directory / f"{camera_id}.json"

    def _read(self, camera_id: str) -> tuple[dict[int, Bucket], bool]:
        """The saved grids that are not too old yet, and whether any were too old."""
        cutoff = self.clock() - KEEP_DAYS * 86400
        path = self._path(camera_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            saved = data.get("hours", {})
            hours = {}
            for hour, b in saved.items():
                if int(hour) + HOUR > cutoff:
                    hours[int(hour)] = {"rows": int(b["rows"]), "cols": int(b["cols"]),
                                        "cells": {int(k): int(v) for k, v in b["cells"].items()}}
            return hours, len(hours) < len(saved)
        except FileNotFoundError:
            return {}, False
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            print(f"[heatmap] Ignoring unreadable heatmap of {camera_id}: {exc}")
            return {}, False

    def _hours(self, camera_id: str) -> dict[int, Bucket]:
        """The camera's grids, read from disk the first time."""
        with self._lock:
            hours = self._maps.get(camera_id)
        if hours is not None:
            return hours
        loaded, expired = self._read(camera_id)
        with self._lock:
            if camera_id not in self._maps and expired:
                self._dirty.add(camera_id)  # the file still holds them
            return self._maps.setdefault(camera_id, loaded)

    def load(self, camera_id: str) -> None:
        """Reads a camera's saved heatmap before its loop starts adding to it."""
        self._hours(camera_id)

    # ---- adding ---------------------------------------------------------------------
    def add(self, camera_id: str, detections: list[Detection], width: int, height: int,
            now: float | None = None) -> int:
        """Adds the foot points of the people detected in a width x height picture. Returns how many."""
        now = self.clock() if now is None else now
        hours = self._hours(camera_id)
        added = 0
        with self._lock:
            for d in detections:
                if d.class_name != "person" or d.simulated:
                    continue
                if d.track_id is not None:
                    key = (camera_id, d.track_id)
                    if now - self._sampled.get(key, float("-inf")) < SAMPLE_SECONDS:
                        continue
                    self._sampled[key] = now
                hour = int(now // HOUR) * HOUR
                bucket = hours.get(hour)
                if bucket is None:
                    bucket = hours[hour] = {"rows": grid_rows(width, height), "cols": COLS, "cells": {}}
                x1, _, x2, y2 = d.bbox
                rows, cols = bucket["rows"], bucket["cols"]
                col = min(cols - 1, max(0, int((x1 + x2) / 2 / width * cols)))
                row = min(rows - 1, max(0, int(y2 / height * rows)))
                cell = row * cols + col
                bucket["cells"][cell] = bucket["cells"].get(cell, 0) + 1
                added += 1
            if added:
                self._dirty.add(camera_id)
        return added

    # ---- reading ---------------------------------------------------------------------
    def _buckets(self, camera_id: str, hours: int, now: float) -> list[tuple[int, Bucket]]:
        since = now - hours * HOUR
        grids = self._hours(camera_id)
        with self._lock:
            return [(h, {**b, "cells": dict(b["cells"])}) for h, b in sorted(grids.items()) if h + HOUR > since]

    def grid(self, camera_id: str, hours: int, now: float | None = None) -> np.ndarray:
        """Points per cell over the last `hours`, in the shape of the latest grid."""
        buckets = self._buckets(camera_id, hours, self.clock() if now is None else now)
        if not buckets:
            return np.zeros((round(COLS * 9 / 16), COLS), np.float32)
        rows, cols = buckets[-1][1]["rows"], buckets[-1][1]["cols"]
        out = np.zeros((rows, cols), np.float32)
        for _, b in buckets:
            for cell, n in b["cells"].items():
                r, c = divmod(cell, b["cols"])
                # A grid from when the picture had another shape is mapped proportionally
                out[min(rows - 1, int((r + 0.5) * rows / b["rows"])), min(cols - 1, int((c + 0.5) * cols / b["cols"]))] += n
        return out

    def summary(self, camera_id: str, hours: int, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        by_hour = [0] * 24  # by this computer's hour of the day
        for start, b in self._buckets(camera_id, hours, now):
            by_hour[datetime.fromtimestamp(start).hour] += sum(b["cells"].values())
        total = sum(by_hour)
        busiest = sorted((h for h in range(24) if by_hour[h]), key=lambda h: (-by_hour[h], h))[:3]
        return {"hours": hours, "total": total, "empty": total == 0, "by_hour": by_hour,
                "busiest_hours": [{"hour": h, "points": by_hour[h], "share": round(by_hour[h] / total, 3)}
                                  for h in busiest]}

    def render(self, camera_id: str, hours: int, picture=None, zones=(), now: float | None = None) -> bytes:
        """A JPEG of the heatmap over `picture` (the camera's latest raw frame, or None for a dark
        placeholder) with the detection zones outlined."""
        grid = self.grid(camera_id, hours, now)
        rows, cols = grid.shape
        if picture is None:
            base = np.full((round(640 * rows / cols), 640, 3), 28, np.uint8)
        else:
            h, w = picture.shape[:2]
            scale = min(1.0, RENDER_WIDTH / w)
            base = cv2.resize(picture, (round(w * scale), round(h * scale))) if scale < 1 else picture.copy()
        out = paint(base, grid)
        h, w = out.shape[:2]
        thick = max(1, round(w / 600))
        for zone in zones:
            points = np.array([[int(x * w), int(y * h)] for x, y in zone], dtype=np.int32)
            cv2.polylines(out, [points], True, ZONE, thick, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise RuntimeError("Could not encode the heatmap")
        return buf.tobytes()

    # ---- saving and clean-up -----------------------------------------------------------
    def save(self, camera_id: str) -> None:
        with self._lock:
            hours = self._maps.get(camera_id)
            if hours is None or camera_id not in self._dirty:
                return
            self._dirty.discard(camera_id)
            data = {"version": 1, "hours": {str(h): {"rows": b["rows"], "cols": b["cols"],
                                                     "cells": {str(k): v for k, v in b["cells"].items()}}
                                            for h, b in hours.items()}}
        path = self._path(camera_id)
        try:
            if not data["hours"]:
                path.unlink(missing_ok=True)
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            print(f"[heatmap] Could not save the heatmap of {camera_id}: {exc}")
            with self._lock:
                self._dirty.add(camera_id)

    def save_all(self) -> None:
        with self._lock:
            dirty = list(self._dirty)
        for camera_id in dirty:
            self.save(camera_id)

    def prune(self, now: float | None = None) -> int:
        """Drops hours older than KEEP_DAYS, also of cameras that no longer exist. Returns how many."""
        now = self.clock() if now is None else now
        cutoff = now - KEEP_DAYS * 86400
        on_disk = {p.stem for p in self.directory.glob("*.json")} if self.directory.is_dir() else set()
        removed = 0
        for camera_id in on_disk | set(self._maps):
            if not _CAMERA_ID.match(camera_id):
                continue
            hours = self._hours(camera_id)
            with self._lock:
                old = [h for h in hours if h + HOUR <= cutoff]
                for h in old:
                    del hours[h]
                if old or (camera_id in on_disk and not hours):
                    self._dirty.add(camera_id)
                removed += len(old)
            self.save(camera_id)
        with self._lock:
            self._sampled = {k: t for k, t in self._sampled.items() if now - t < TRACK_MEMORY}
        return removed

    def forget(self, camera_id: str) -> None:
        """Deletes a camera's heatmap, e.g. when the camera is removed."""
        with self._lock:
            self._maps.pop(camera_id, None)
            self._dirty.discard(camera_id)
        try:
            self._path(camera_id).unlink(missing_ok=True)
        except (OSError, KeyError):
            pass

    def tick(self, now: float | None = None) -> None:
        """Called from the manager's background loop: saves every few minutes, cleans up hourly."""
        now = self.clock() if now is None else now
        if now - self._saved_at >= SAVE_SECONDS:
            self._saved_at = now
            self.save_all()
        if now - self._pruned_at >= PRUNE_SECONDS:
            self._pruned_at = now
            self.prune(now)


def paint(base: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Colours the places with activity: blurred, scaled to the busiest place, blended over `base`
    only where there was activity, so the rest of the picture stays as it is."""
    if not grid.any():
        return base
    h, w = base.shape[:2]
    heat = cv2.GaussianBlur(grid.astype(np.float32), (0, 0), 1.2)
    heat = np.clip(cv2.resize(heat, (w, h), interpolation=cv2.INTER_CUBIC), 0, None)
    level = np.sqrt(heat / heat.max())  # so places visited less often still show
    colours = cv2.applyColorMap((level * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    alpha = np.where(level > 0.12, np.clip(0.25 + level * 0.5, 0, 0.7), 0)[..., None]
    return (base * (1 - alpha) + colours * alpha).astype(np.uint8)


heatmap_service = HeatmapService()
