import os
import threading
import time
from typing import List

from config import YOLO_MODEL
from models.domain import Detection


# COCO classes that can be left behind; the lower confidence suits small, still objects.
OBJECT_CLASSES = {24: "backpack", 26: "handbag", 28: "suitcase"}
OBJECT_CONFIDENCE = 0.35


class DetectionService:
    """YOLOv8 person detector. The model is loaded on first use."""

    def __init__(self, model_path: str = YOLO_MODEL):
        self.model_path = model_path
        self._model = None
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()
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
        return self.detect(frame, confidence, min_height_pct)[0]

    def detect(self, frame, confidence: float = 0.5, min_height_pct: int = 10,
               objects: bool = False) -> tuple[List[Detection], List[Detection]]:
        """People, and with `objects` also bags and suitcases (for unattended-object alerts), in one pass."""
        if not self.load():
            return [], []
        classes = [0, *OBJECT_CLASSES] if objects else [0]
        # One model is shared by every camera; YOLO models are not safe to call concurrently.
        with self._infer_lock:
            started = time.perf_counter()
            results = self._model(frame, classes=classes, conf=min(confidence, OBJECT_CONFIDENCE) if objects
                                  else confidence, imgsz=640, verbose=False)
            self.inference_ms = round((time.perf_counter() - started) * 1000, 1)

        frame_h = frame.shape[0]
        min_h = frame_h * min_height_pct / 100.0
        persons: List[Detection] = []
        things: List[Detection] = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                cls, conf = int(box.cls[0]) if box.cls is not None else 0, float(box.conf[0])
                if cls == 0:
                    if (y2 - y1) < min_h or conf < confidence:
                        continue
                    persons.append(Detection(class_name="person", confidence=conf, bbox=[x1, y1, x2, y2]))
                elif cls in OBJECT_CLASSES:
                    things.append(Detection(class_name=OBJECT_CLASSES[cls], confidence=conf, bbox=[x1, y1, x2, y2]))
        return persons, things

    def status(self) -> dict:
        return {"loaded": self.loaded, "model": os.path.basename(self.model_path),
                "device": self.device, "inference_ms": self.inference_ms, "error": self.error}


detection_service = DetectionService()
