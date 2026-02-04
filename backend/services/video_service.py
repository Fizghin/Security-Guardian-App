import cv2
import os
import time
import threading
from typing import Generator, Optional


def detect_available_camera(max_index: int = 10) -> tuple:
    """Auto-detect the first available camera. Returns (index, backend_code)."""
    print("=" * 50)
    print("AUTO-DETECTING AVAILABLE CAMERAS...")
    print("=" * 50)
    
    for index in range(max_index):
        print(f"  Testing camera index {index}...", end=" ")
        
        # Try DirectShow first (better Windows compatibility)
        try:
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    print(f"[OK] DSHOW working (frame: {frame.shape})")
                    print(f">>> Selected camera index: {index} with DirectShow")
                    print("=" * 50)
                    return index, cv2.CAP_DSHOW
                else:
                    print("[X] DSHOW opens but no frame", end=" ")
            else:
                print("[X] DSHOW can't open", end=" ")
            cap.release()
        except Exception as e:
            print(f"[X] DSHOW error: {e}", end=" ")
        
        # Try default backend
        try:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    print(f"[OK] DEFAULT working")
                    print(f">>> Selected camera index: {index} with default backend")
                    print("=" * 50)
                    return index, cv2.CAP_ANY
            cap.release()
        except:
            pass
        
        print("")  # Newline after all attempts for this index
    
    print("[WARNING] NO WORKING CAMERAS FOUND - defaulting to index 0")
    print("=" * 50)
    return 0, cv2.CAP_ANY


class VideoService:
    def __init__(self, source="auto"):
        print(f"\n[VideoService] Initializing with source={source}")
        
        self.backend = cv2.CAP_DSHOW  # Default to DirectShow on Windows
        
        # Auto-detect camera if source is "auto" or None
        if source is None or source == "auto":
            self.source, self.backend = detect_available_camera()
        else:
            self.source = source
            
        self.camera = None
        self.is_running = False
        self.lock = threading.Lock()
        self.current_frame = None
        self._failed_reads = 0
        self._max_failed_reads = 30
        self._camera_ok = False

    def _open_camera(self, source, backend=None):
        """Open camera with best available backend."""
        if backend is None:
            backend = self.backend
            
        try:
            cap = cv2.VideoCapture(source, backend)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    return cap, True
            cap.release()
        except:
            pass
        
        # Fallback to default backend
        try:
            cap = cv2.VideoCapture(source)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    return cap, True
            cap.release()
        except:
            pass
            
        return None, False

    def start(self):
        if self.is_running:
            print("[VideoService] Already running")
            return
        
        print(f"[VideoService] Starting on source {self.source} with backend {self.backend}...")
        
        self.camera, success = self._open_camera(self.source, self.backend)
        
        if not success:
            print(f"[VideoService] [WARNING] Could not open camera {self.source}. Attempting auto-detection...")
            self._try_alternate_camera()
        else:
            # Read initial frame
            ret, test_frame = self.camera.read()
            if ret and test_frame is not None:
                print(f"[VideoService] [OK] Camera {self.source} confirmed working. Frame shape: {test_frame.shape}")
                self._camera_ok = True
                with self.lock:
                    self.current_frame = test_frame
            else:
                print(f"[VideoService] [WARNING] Camera opened but can't read. Trying alternate...")
                self._try_alternate_camera()
        
        self.is_running = True
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()
        print(f"[VideoService] [OK] Update thread started")

    def _try_alternate_camera(self):
        """Try to find and switch to an alternate working camera."""
        if self.camera:
            try:
                self.camera.release()
            except:
                pass
            
        new_source, new_backend = detect_available_camera()
        print(f"[VideoService] Switching from camera {self.source} to {new_source}")
        self.source = new_source
        self.backend = new_backend
        
        self.camera, success = self._open_camera(self.source, self.backend)
        
        if success:
            ret, frame = self.camera.read()
            if ret and frame is not None:
                self._camera_ok = True
                with self.lock:
                    self.current_frame = frame
                print(f"[VideoService] [OK] Alternate camera {new_source} working")
                return
                
        self._camera_ok = False
        print("[VideoService] [X] No working camera found. Running in passive mode.")

    def _update(self):
        while self.is_running:
            if not self.camera or not self.camera.isOpened():
                time.sleep(0.5)
                continue
                
            try:
                ret, frame = self.camera.read()
                if ret and frame is not None:
                    self._failed_reads = 0
                    self._camera_ok = True
                    with self.lock:
                        self.current_frame = frame
                else:
                    self._failed_reads += 1
                    if self._failed_reads >= self._max_failed_reads:
                        print(f"[VideoService] [WARNING] Camera {self.source} unresponsive. Retrying...")
                        self._try_alternate_camera()
                        self._failed_reads = 0
                    time.sleep(0.1)
            except Exception as e:
                print(f"[VideoService] Read error: {e}")
                time.sleep(0.5)

    def get_frame(self) -> Optional[object]:
        with self.lock:
            if self.current_frame is not None:
                return self.current_frame.copy()
            return None

    def generate_frames(self) -> Generator[bytes, None, None]:
        while True:
            frame = self.get_frame()
            if frame is None:
                time.sleep(0.01)
                continue
            
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    def stop(self):
        print("[VideoService] Stopping...")
        self.is_running = False
        if self.camera:
            try:
                self.camera.release()
            except:
                pass
        print("[VideoService] Stopped")
        
    def get_status(self) -> dict:
        """Get current camera status for debugging."""
        return {
            "source": self.source,
            "is_running": self.is_running,
            "camera_ok": self._camera_ok,
            "has_frame": self.current_frame is not None,
            "failed_reads": self._failed_reads
        }


# Source can be "auto" (auto-detect), 0/1/2... (webcam index), or a URL string (IP cam)
_video_source = os.getenv("VIDEO_SOURCE", "auto")
if _video_source.isdigit():
    _video_source = int(_video_source)

print(f"\n{'='*50}")
print("INITIALIZING VIDEO SERVICE")
print(f"VIDEO_SOURCE env: {os.getenv('VIDEO_SOURCE', 'not set (using auto)')}")
print(f"{'='*50}\n")

video_service = VideoService(source=_video_source)
