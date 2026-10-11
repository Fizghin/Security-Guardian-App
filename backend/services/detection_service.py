import os
import threading
import time
from typing import List

from config import YOLO_MODEL
from models.domain import Detection

# Things that can be left behind, found in the same run as people (COCO class ids).
BAG_CLASSES = {24: "backpack", 26: "handbag", 28: "suitcase"}
BAG_CONFIDENCE = 0.35  # bags are smaller and harder to see than people


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
               bags: bool = False) -> tuple[List[Detection], List[Detection]]:
        """People and, with bags=True, backpacks, handbags and suitcases from the same run. Bags have their
        own confidence and no minimum size. People come out exactly as without bags: the model keeps the
        strongest box of each class, so a weaker box or a bag never replaces a person."""
        if not self.load():
            return [], []
        classes = [0, *BAG_CLASSES] if bags else [0]
        # One model is shared by every camera; YOLO models are not safe to call concurrently.
        with self._infer_lock:
            started = time.perf_counter()
            results = self._model(frame, classes=classes, conf=min(confidence, BAG_CONFIDENCE) if bags else confidence,
                                  imgsz=640, verbose=False)
            self.inference_ms = round((time.perf_counter() - started) * 1000, 1)

        frame_h = frame.shape[0]
        min_h = frame_h * min_height_pct / 100.0
        detections: List[Detection] = []
        found_bags: List[Detection] = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                score, kind = float(box.conf[0]), int(box.cls[0])
                if kind in BAG_CLASSES:
                    if score >= BAG_CONFIDENCE:
                        found_bags.append(Detection(class_name=BAG_CLASSES[kind], confidence=score, bbox=[x1, y1, x2, y2]))
                    continue
                if score < confidence or (y2 - y1) < min_h:
                    continue
                detections.append(Detection(class_name="person", confidence=score, bbox=[x1, y1, x2, y2]))
        return detections, found_bags

    def status(self) -> dict:
        return {"loaded": self.loaded, "model": os.path.basename(self.model_path),
                "device": self.device, "inference_ms": self.inference_ms, "error": self.error}


detection_service = DetectionService()
