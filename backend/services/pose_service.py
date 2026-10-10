"""
Body keypoints (YOLOv8n-pose) for people who may be lying down; see fall_watch.py.

The model (about 7 MB) is downloaded into the models folder the first time it is
needed, in the background, so a camera never waits for it. It only ever looks at
the boxes of people the fall check asks about, never at whole pictures.
"""
import threading
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

from config import MODELS_DIR
from services.detection_service import detection_service
from services.tracker import iou

POSE_FILE = "yolov8n-pose.pt"
POSE_URL = "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n-pose.pt"
MARGIN = 0.15        # share of a person's box added around it, so arms and feet aren't cut off
POSE_IMGSZ = 320     # a crop of one person needs far fewer pixels than a whole picture
POSE_CONFIDENCE = 0.25
RETRY_SECONDS = 600  # after a failed download or load, try again this much later
TURNS = (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE)
TORSO = (5, 6, 11, 12)  # shoulders and hips: how clearly a reading shows them decides between turns
TURN_MARGIN = 0.2       # a turned reading counts only when it shows them this much more clearly than the upright one


def _turn_points(points: np.ndarray, turn, w: int, h: int) -> np.ndarray:
    """Points on a w x h picture, where they are once the picture is turned."""
    x, y = points[:, 0], points[:, 1]
    if turn == cv2.ROTATE_90_CLOCKWISE:
        return np.stack([h - y, x], axis=1)
    if turn == cv2.ROTATE_90_COUNTERCLOCKWISE:
        return np.stack([y, w - x], axis=1)
    return points.copy()


def _turn_back(points: np.ndarray, turn, w: int, h: int) -> np.ndarray:
    """Points on the turned picture, back on the original w x h picture."""
    u, v = points[:, 0], points[:, 1]
    if turn == cv2.ROTATE_90_CLOCKWISE:
        return np.stack([v, h - u], axis=1)
    if turn == cv2.ROTATE_90_COUNTERCLOCKWISE:
        return np.stack([w - v, u], axis=1)
    return points.copy()


class PoseService:
    def __init__(self, models_dir: Path = MODELS_DIR, lock: threading.Lock | None = None):
        self.path = models_dir / POSE_FILE
        # The person detector's lock: one model runs at a time, whichever camera asks.
        self._infer_lock = lock or detection_service._infer_lock
        self._model = None
        self._loading = False
        self._failed_at: float | None = None
        self.error: str | None = None

    @property
    def state(self) -> str:
        if self._model is not None:
            return "ready"
        return "loading" if self._loading else "error" if self.error else "idle"

    def ready(self) -> bool:
        """Whether poses can be estimated now. The first call starts loading the model in the background."""
        if self._model is not None:
            return True
        if not self._loading and (self._failed_at is None or time.monotonic() - self._failed_at >= RETRY_SECONDS):
            self._loading = True
            threading.Thread(target=self._load, daemon=True, name="pose-init").start()
        return False

    def _download(self) -> None:
        print(f"[pose] Downloading {POSE_FILE}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".part")
        with httpx.stream("GET", POSE_URL, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 16):
                    fh.write(chunk)
        tmp.replace(self.path)

    def _load(self) -> None:
        try:
            if not self.path.exists():
                self._download()
            from ultralytics import YOLO
            self._model = YOLO(str(self.path))
            self.error, self._failed_at = None, None
            print("[pose] Ready")
        except Exception as exc:
            self.error = f"Pose model unavailable: {exc}. Place {POSE_FILE} in {self.path.parent} to check for falls offline."
            self._failed_at = time.monotonic()
            print(f"[pose] {self.error}")
        finally:
            self._loading = False

    def estimate(self, frame, boxes: list[list[float]]) -> list[np.ndarray | None]:
        """17 COCO keypoints (x, y, confidence) in frame pixels for the person in each box, or None
        where the model found nobody.

        The model has mostly seen people standing up and reads someone lying down poorly, so each crop
        is also looked at turned a quarter both ways (where that person looks upright). A turned reading
        only counts when it shows the shoulders and hips clearly better than the upright one, because
        the model sometimes reads a standing person as lying when they are turned."""
        if not boxes or self._model is None:
            return [None] * len(boxes)
        h, w = frame.shape[:2]
        crops, wanted = [], []
        for x1, y1, x2, y2 in boxes:
            mx, my = (x2 - x1) * MARGIN, (y2 - y1) * MARGIN
            cx1, cy1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
            cx2, cy2 = min(w, int(x2 + mx)), min(h, int(y2 + my))
            crop = frame[cy1:cy2, cx1:cx2]
            box = np.array([[x1 - cx1, y1 - cy1], [x2 - cx1, y2 - cy1]], np.float32)
            for turn in TURNS:
                crops.append(crop if turn is None else cv2.rotate(crop, turn))
                corners = _turn_points(box, turn, crop.shape[1], crop.shape[0])
                wanted.append((turn, crop.shape[1], crop.shape[0], (cx1, cy1),
                               [*corners.min(axis=0).tolist(), *corners.max(axis=0).tolist()]))
        with self._infer_lock:
            results = self._model(crops, imgsz=POSE_IMGSZ, conf=POSE_CONFIDENCE, verbose=False)
        out: list[np.ndarray | None] = []
        for i in range(0, len(results), len(TURNS)):
            best, best_score = None, 0.0
            for result, (turn, cw, ch, (ox, oy), box) in zip(results[i:i + len(TURNS)], wanted[i:i + len(TURNS)]):
                if result.keypoints is None or len(result.boxes) == 0:
                    continue
                # The crop can show someone else too: take the pose that fits the person asked about.
                fits = [iou(box, b.tolist()) for b in result.boxes.xyxy]
                pick = int(np.argmax(fits))
                if fits[pick] < 0.3:
                    continue
                points = result.keypoints.data[pick].cpu().numpy().astype(np.float32).copy()
                score = float(points[list(TORSO), 2].mean()) + (TURN_MARGIN if turn is None else 0.0)
                if score > best_score:
                    points[:, :2] = _turn_back(points[:, :2], turn, cw, ch)
                    points[:, 0] += ox
                    points[:, 1] += oy
                    best, best_score = points, score
            out.append(best)
        return out

    def status(self) -> dict:
        return {"state": self.state, "error": self.error}


pose_service = PoseService()
