"""
Camera capture.

A single background thread owns the capture device and keeps the most recent
frame. Sources:
  "auto"            first working local camera
  "0", "1", ...     local camera index (webcam, DroidCam virtual camera, ...)
  rtsp://, http://  IP camera / DroidCam / phone stream
  path/to/file.mp4  a video file, looped (handy for testing)
  "none"            camera disabled
"""
import os
import platform
import threading
import time

import cv2

SYSTEM = platform.system()


def _local_backends():
    if SYSTEM == "Windows":
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    if SYSTEM == "Darwin":
        return [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY]
    return [cv2.CAP_V4L2, cv2.CAP_ANY]


def parse_source(source: str):
    """Returns (kind, value) where kind is none|auto|index|url|file."""
    s = (source or "").strip()
    if not s or s.lower() == "none":
        return "none", None
    if s.lower() == "auto":
        return "auto", None
    if s.isdigit():
        return "index", int(s)
    if "://" in s:
        return "url", s
    return "file", os.path.expanduser(s)


def open_local_camera(index: int):
    for backend in _local_backends():
        try:
            cap = cv2.VideoCapture(index, backend)
        except Exception:
            continue
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                return cap
        cap.release()
    return None


def scan_local_cameras(max_index: int = 6, skip: set[int] | None = None) -> list[dict]:
    found = []
    for index in range(max_index):
        if skip and index in skip:
            continue
        cap = open_local_camera(index)
        if cap is None:
            continue
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        found.append({"index": index, "width": w, "height": h})
    return found


class VideoService:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frame = None
        self._frame_id = 0
        self._frame_time = 0.0
        self.source = "none"
        self.kind = "none"
        self.active_index: int | None = None
        self.connected = False
        self.error: str | None = None
        self.width = 0
        self.height = 0
        self.fps = 0.0

    # ---- lifecycle -----------------------------------------------------
    def start(self, source: str) -> None:
        self.stop()
        self.source = source
        self.kind, _ = parse_source(source)
        self.error = None
        if self.kind == "none":
            self.error = "Camera disabled"
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="video")
        self._thread.start()

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=5)
        self._thread = None
        with self._lock:
            self._frame = None
        self.connected = False
        self.fps = 0.0
        self.active_index = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- capture loop --------------------------------------------------
    def _open(self):
        kind, value = parse_source(self.source)
        if kind == "auto":
            for index in range(6):
                if self._stop.is_set():
                    return None
                cap = open_local_camera(index)
                if cap is not None:
                    self.active_index = index
                    return cap
            raise RuntimeError("No local camera found")
        if kind == "index":
            cap = open_local_camera(value)
            if cap is None:
                raise RuntimeError(f"Camera {value} could not be opened (in use by another app, or not connected)")
            self.active_index = value
            return cap
        if kind == "file" and not os.path.isfile(value):
            raise RuntimeError(f"Video file not found: {value}")
        cap = cv2.VideoCapture(value)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open stream {value}")
        if kind == "url":
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # keep latency low on IP cameras
        return cap

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                cap = self._open()
            except Exception as exc:
                self.error = str(exc)
                self.connected = False
                print(f"[video] {exc}. Retrying in {backoff:.0f}s")
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 15.0)
                continue
            if cap is None:
                break

            backoff = 1.0
            self.error = None
            is_file = self.kind == "file"
            file_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            if not 1 <= file_fps <= 120:
                file_fps = 25.0
            failures, count, window_start = 0, 0, time.time()
            print(f"[video] Connected to {self.source}")

            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    if is_file:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the file
                        failures += 1
                        if failures < 3:
                            continue
                    failures += 1
                    if failures >= 30:
                        self.error = "Camera stopped sending frames"
                        print(f"[video] {self.error}; reconnecting")
                        break
                    time.sleep(0.05)
                    continue
                failures = 0
                with self._lock:
                    self._frame = frame
                    self._frame_id += 1
                    self._frame_time = time.time()
                self.connected = True
                self.height, self.width = frame.shape[:2]

                count += 1
                elapsed = time.time() - window_start
                if elapsed >= 2.0:
                    self.fps = round(count / elapsed, 1)
                    count, window_start = 0, time.time()
                if is_file:
                    time.sleep(1.0 / file_fps)

            cap.release()
            self.connected = False
            self.fps = 0.0
            if not self._stop.is_set():
                self._stop.wait(1.0)

    # ---- access --------------------------------------------------------
    def get_frame(self):
        """Returns (frame, frame_id, timestamp) or (None, id, 0). The frame must not be modified."""
        with self._lock:
            return self._frame, self._frame_id, self._frame_time

    def status(self) -> dict:
        age = time.time() - self._frame_time if self._frame_time else None
        live = self.connected and age is not None and age < 3
        return {
            "source": self.source,
            "kind": self.kind,
            "active_index": self.active_index,
            "connected": live,
            "error": self.error if not live else None,
            "width": self.width,
            "height": self.height,
            "fps": self.fps if live else 0.0,
        }


video_service = VideoService()
