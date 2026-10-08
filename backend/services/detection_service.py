import os
import threading
import time
from typing import List

from config import YOLO_MODEL
from models.domain import Detection


class DetectionService:
    """YOLOv8 person detector. The model is loaded on first use."""

    def __init__(self, model_path: str = YOLO_MODEL):
        self.model_path = model_path
        self._model = None
        self._lock = threading.Lock()
        self.error: str | None = None
        self.device: str | None = None
        self.inference_ms: float | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        with self._lock:
            if self._model is not None:
                return True
            try:
                from ultralytics import YOLO
                print(f"[detect] Loading {self.model_path}")
                self._model = YOLO(self.model_path)
                try:
                    import torch
                    self.device = "cuda" if torch.cuda.is_available() else "cpu"
                    if self.device == "cpu":
                        # Leave cores for the local language model (see ai_service.LLM_THREADS).
                        torch.set_num_threads(max(1, (os.cpu_count() or 4) // 2))
                except Exception:
                    self.device = "cpu"
                self.error = None
                return True
            except Exception as exc:
                self.error = f"Could not load YOLO model: {exc}"
                print(f"[detect] {self.error}")
                return False

    def detect_persons(self, frame, confidence: float = 0.5, min_height_pct: int = 10) -> List[Detection]:
        if not self.load():
            return []
        started = time.perf_counter()
        results = self._model(frame, classes=[0], conf=confidence, imgsz=640, verbose=False)
        self.inference_ms = round((time.perf_counter() - started) * 1000, 1)

        frame_h = frame.shape[0]
        min_h = frame_h * min_height_pct / 100.0
        detections: List[Detection] = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                if (y2 - y1) < min_h:
                    continue
                detections.append(Detection(class_name="person", confidence=float(box.conf[0]), bbox=[x1, y1, x2, y2]))
        return detections

    def status(self) -> dict:
        return {"loaded": self.loaded, "model": os.path.basename(self.model_path),
                "device": self.device, "inference_ms": self.inference_ms, "error": self.error}


detection_service = DetectionService()
