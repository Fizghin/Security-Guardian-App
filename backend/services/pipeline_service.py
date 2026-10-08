"""
The monitoring loop. Runs in one background thread for the life of the server,
whether or not anyone has the dashboard open:

  camera frame -> motion check -> person detection -> insider recognition
               -> threat logic -> annotated frame (dashboard stream + recordings)
"""
import os
import threading
import time
from datetime import datetime

import cv2

from models.domain import Detection
from services.brain_service import brain_service
from services.detection_service import detection_service
from services.face_service import face_service
from services.recording_service import recording_service
from services.settings_service import settings_service
from services.video_service import video_service

# Avoid oversubscribing the CPU: OpenCV, PyTorch and the language model all default to one
# thread per core, and fighting over cores makes every one of them slower.
cv2.setNumThreads(max(1, (os.cpu_count() or 4) // 2))

STREAM_MAX_WIDTH = 960
HEARTBEAT_SECONDS = 2.0  # run detection at least this often even without motion
BOX_HOLD_SECONDS = 1.5

RED, GREEN, AMBER, GREY = (40, 40, 220), (90, 180, 60), (0, 170, 240), (150, 150, 150)


def draw_overlay(frame, detections: list[Detection], camera_name: str, armed: bool):
    # Text and lines scale with the frame so labels stay readable on HD cameras.
    scale = max(0.5, frame.shape[1] / 1100)
    thick = max(1, round(scale * 1.5))
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in d.bbox)
        if d.simulated:
            color, label = AMBER, "TEST"
        elif d.known:
            color, label = GREEN, d.identity or "Insider"
        else:
            color, label = (RED if armed else GREY), "Unknown"
        label = f"{label} {d.confidence:.0%}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thick + 1)
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale, thick)
        pad = int(4 * scale)
        ty = max(y1, th + base + 2 * pad)
        cv2.rectangle(frame, (x1, ty - th - base - 2 * pad), (x1 + tw + 2 * pad, ty), color, -1)
        cv2.putText(frame, label, (x1 + pad, ty - base - pad), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale,
                    (255, 255, 255), thick, cv2.LINE_AA)

    stamp = f"{camera_name}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    h = frame.shape[0]
    (tw, th), base = cv2.getTextSize(stamp, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, thick)
    pad = int(5 * scale)
    cv2.rectangle(frame, (pad, h - th - base - 3 * pad), (tw + 3 * pad, h - pad), (0, 0, 0), -1)
    cv2.putText(frame, stamp, (2 * pad, h - base - 2 * pad), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale,
                (235, 235, 235), thick, cv2.LINE_AA)
    return frame


class PipelineService:
    def __init__(self, video=video_service, detector=detection_service, faces=face_service, brain=brain_service,
                 recorder=recording_service, settings=settings_service):
        self.video, self.detector, self.faces, self.brain = video, detector, faces, brain
        self.recorder, self.settings = recorder, settings
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._annotated = None
        self._jpeg: bytes | None = None
        self._jpeg_id = 0
        self._detections: list[Detection] = []
        self._detections_time = 0.0
        self.simulate_until = 0.0
        self.fps = 0.0
        self.motion = False
        self.error: str | None = None

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True, name="pipeline")
        self._thread.start()
        self.recorder.start_sampler(self.latest_frame)
        self.brain.snapshot = self.snapshot

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)

    def simulate(self, seconds: float) -> None:
        self.simulate_until = time.time() + seconds

    # ---- outputs ------------------------------------------------------------
    def latest_frame(self):
        with self._lock:
            return self._annotated

    def latest_jpeg(self) -> tuple[bytes | None, int]:
        with self._lock:
            return self._jpeg, self._jpeg_id

    def snapshot(self) -> bytes | None:
        return self.latest_jpeg()[0]

    # ---- loop ---------------------------------------------------------------
    def _run(self) -> None:
        prev_gray = None
        last_frame_id = -1
        last_detect = 0.0
        last_tick = 0.0
        count, window = 0, time.time()

        while self._running.is_set():
            now = time.time()
            if now - last_tick >= 0.25:
                self.brain.tick(now)
                last_tick = now

            frame, frame_id, _ = self.video.get_frame()
            if frame is None or frame_id == last_frame_id:
                time.sleep(0.01)
                continue
            last_frame_id = frame_id
            cfg = self.settings.get()

            try:
                # Cheap motion check on a tiny greyscale copy decides whether YOLO needs to run.
                h, w = frame.shape[:2]
                small = cv2.resize(frame, (160, max(1, int(160 * h / w))))
                gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
                if prev_gray is not None and prev_gray.shape == gray.shape:
                    diff = cv2.threshold(cv2.absdiff(prev_gray, gray), 25, 255, cv2.THRESH_BINARY)[1]
                    self.motion = cv2.countNonZero(diff) > gray.size * 0.004
                prev_gray = gray

                simulating = now < self.simulate_until
                in_incident = self.brain.incident_start is not None
                interval = cfg.detection.interval_ms / 1000.0
                if now - last_detect >= interval and (
                        self.motion or in_incident or simulating or bool(self._detections)
                        or now - last_detect >= HEARTBEAT_SECONDS):
                    last_detect = now
                    detections = self.detector.detect_persons(
                        frame, cfg.detection.confidence, cfg.detection.min_person_height)
                    if detections and cfg.detection.face_recognition:
                        self.faces.identify(frame, detections, cfg.detection.face_match_threshold)
                    if simulating:
                        detections.append(Detection(class_name="person", confidence=0.99, simulated=True,
                                                    bbox=[w * 0.35, h * 0.2, w * 0.65, h * 0.95]))
                    self._detections, self._detections_time = detections, now
                    self.brain.process(detections, now)
                    self.error = self.detector.error

                shown = self._detections if now - self._detections_time < BOX_HOLD_SECONDS else []
                annotated = draw_overlay(frame.copy(), shown, cfg.camera.name, cfg.armed)
                display = annotated
                if w > STREAM_MAX_WIDTH:
                    display = cv2.resize(annotated, (STREAM_MAX_WIDTH, int(h * STREAM_MAX_WIDTH / w)))
                ok, buf = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 75])
                with self._lock:
                    self._annotated = annotated
                    if ok:
                        self._jpeg = buf.tobytes()
                        self._jpeg_id += 1
            except Exception as exc:
                self.error = f"Processing error: {exc}"
                print(f"[pipeline] {self.error}")
                time.sleep(0.5)

            count += 1
            if now - window >= 2.0:
                self.fps = round(count / (now - window), 1)
                count, window = 0, now

    def status(self) -> dict:
        remaining = max(0, int(self.simulate_until - time.time()))
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "fps": self.fps if self.video.status()["connected"] else 0.0,
            "motion": self.motion,
            "test_seconds_left": remaining,
            "error": self.error,
        }


pipeline_service = PipelineService()
