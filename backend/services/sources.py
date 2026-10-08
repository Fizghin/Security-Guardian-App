"""
Camera frame sources.

CaptureSource pulls frames with OpenCV from:
  "auto"            first working local camera
  "0", "1", ...     local camera index (webcam, DroidCam virtual camera, ...)
  rtsp://, http://  IP camera, or a phone running an IP-camera app
  path/to/file.mp4  a video file, looped (handy for testing)
  "none"            disabled

PhoneSource receives JPEG frames pushed by a phone's browser (see phone_service).

Both expose get_frame() -> (frame, frame_id, timestamp) and status().
"""
import os
import platform
import threading
import time

import cv2

from services.camera_access import local_camera_blocked

SYSTEM = platform.system()
PHONE_TIMEOUT = 4.0  # seconds without a frame before a phone counts as offline


class CameraBlocked(RuntimeError):
    """The operating system doesn't allow camera access (yet)."""


def _local_backends():
    if SYSTEM == "Windows":
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    if SYSTEM == "Darwin":
        return [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY]
    return [cv2.CAP_V4L2, cv2.CAP_ANY]


def parse_source(source: str):
    """Returns (kind, value) where kind is none|auto|index|url|file|phone."""
    s = (source or "").strip()
    if not s or s.lower() == "none":
        return "none", None
    if s.lower() == "auto":
        return "auto", None
    if s.lower() == "phone":
        return "phone", None
    if s.isdigit():
        return "index", int(s)
    if "://" in s:
        return "url", s
    return "file", os.path.expanduser(s)


def open_local_camera(index: int):
    if local_camera_blocked():
        return None
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


def grab_test_frame(source: str, timeout: float = 10.0):
    """Open a source once and return (frame, error). Used to check a camera before saving it."""
    kind, value = parse_source(source)
    if kind in ("none", "phone", "auto"):
        return None, "Only camera numbers, stream URLs and files can be tested"
    if kind == "file" and not os.path.isfile(value):
        return None, f"File not found: {value}"
    if kind == "index" and (blocked := local_camera_blocked()):
        return None, blocked
    result: dict = {}

    def run():
        try:
            cap = open_local_camera(value) if kind == "index" else cv2.VideoCapture(value)
            if cap is None or not cap.isOpened():
                result["error"] = "Could not connect. Check the address and that the phone/camera app is running"
                return
            ok, frame = cap.read()
            cap.release()
            if ok and frame is not None:
                result["frame"] = frame
            else:
                result["error"] = "Connected but no picture arrived"
        except Exception as exc:
            result["error"] = str(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, f"No answer within {int(timeout)} seconds. Is the phone on the same Wi-Fi?"
    return result.get("frame"), result.get("error")


class CaptureSource:
    def __init__(self, source: str):
        self.source = source
        self.kind, _ = parse_source(source)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frame = None
        self._frame_id = 0
        self._frame_time = 0.0
        self.active_index: int | None = None
        self.connected = False
        self.error: str | None = "Camera disabled" if self.kind == "none" else None
        self.width = 0
        self.height = 0
        self.fps = 0.0

    def start(self) -> None:
        if self.kind == "none" or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"capture:{self.source}")
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

    def _open(self):
        kind, value = parse_source(self.source)
        if kind in ("auto", "index") and (blocked := local_camera_blocked()):
            raise CameraBlocked(blocked)
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
                if self._stop.wait(backoff):
                    break
                # Checking the permission is free, so pick up a "yes" quickly. Searching for a webcam
                # that isn't there makes macOS print warnings, so do that rarely.
                limit = 2.0 if isinstance(exc, CameraBlocked) else 60.0 if self.kind == "auto" else 15.0
                backoff = min(backoff * 2, limit)
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

            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if is_file and failures < 3:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the file
                        continue
                    if failures >= 30:
                        self.error = "Camera stopped sending frames"
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

    def get_frame(self):
        with self._lock:
            return self._frame, self._frame_id, self._frame_time

    def status(self) -> dict:
        age = time.time() - self._frame_time if self._frame_time else None
        live = self.connected and age is not None and age < 3
        return {"kind": self.kind, "active_index": self.active_index, "connected": live,
                "error": None if live else (self.error or "Connecting…"),
                "width": self.width, "height": self.height, "fps": self.fps if live else 0.0}


class PhoneSource:
    """Frames pushed by a phone browser; created per phone camera."""
    kind = "phone"

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._frame_id = 0
        self._frame_time = 0.0
        self._times: list[float] = []
        self.width = self.height = 0
        self.info: dict = {}
        self.last_contact = 0.0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        with self._lock:
            self._frame = None

    def push(self, frame) -> None:
        now = time.time()
        with self._lock:
            self._frame = frame
            self._frame_id += 1
            self._frame_time = now
            self._times = [t for t in self._times if now - t < 3] + [now]
        self.last_contact = now
        self.height, self.width = frame.shape[:2]

    def touch(self, info: dict) -> None:
        self.last_contact = time.time()
        self.info.update({k: v for k, v in info.items() if v is not None})

    def get_frame(self):
        with self._lock:
            if time.time() - self._frame_time > PHONE_TIMEOUT:
                return None, self._frame_id, 0.0
            return self._frame, self._frame_id, self._frame_time

    def status(self) -> dict:
        now = time.time()
        live = now - self._frame_time < PHONE_TIMEOUT
        reachable = now - self.last_contact < PHONE_TIMEOUT
        if live:
            error = None
        elif reachable:
            error = "Phone is connected but its camera is paused"
        elif self.last_contact:
            error = "Phone is offline. Open the camera page on the phone again"
        else:
            error = "Waiting for the phone. Scan the pairing code with it"
        times = [t for t in self._times if now - t < 3]
        fps = round((len(times) - 1) / (times[-1] - times[0]), 1) if live and len(times) > 2 else 0.0
        return {"kind": "phone", "active_index": None, "connected": live, "error": error,
                "width": self.width, "height": self.height, "fps": fps,
                "phone": {**self.info, "online": reachable, "last_contact": self.last_contact or None}}
