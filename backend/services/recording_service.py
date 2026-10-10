"""
Incident recording, one Recorder per camera plus a shared RecordingLibrary.

A sampler thread takes the camera's latest annotated frame at a fixed rate. While idle
it keeps a short pre-roll buffer; when recording it writes frames to an H.264
MP4 through FFmpeg (system install, or the binary bundled with imageio-ffmpeg).
If no FFmpeg is available, OpenCV's MPEG-4 writer is used instead; those clips
download fine but most browsers cannot play them inline.
"""
import json
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import cv2

from config import RECORDINGS_DIR

FPS = 10
MAX_WIDTH = 1280


def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class _FFmpegWriter:
    codec, playable = "h264", True

    def __init__(self, exe: str, path: Path, size: tuple[int, int]):
        w, h = size
        self.proc = subprocess.Popen(
            [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-threads", "1",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def write(self, frame) -> None:
        self.proc.stdin.write(frame.tobytes())

    def close(self) -> bool:
        try:
            self.proc.stdin.close()
            _, err = self.proc.communicate(timeout=60)
        except Exception:
            self.proc.kill()
            return False
        if self.proc.returncode != 0:
            print(f"[rec] ffmpeg failed: {err.decode(errors='ignore')[-300:]}")
        return self.proc.returncode == 0


class _OpenCVWriter:
    codec, playable = "mp4v", False

    def __init__(self, path: Path, size: tuple[int, int]):
        self.writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, size)
        if not self.writer.isOpened():
            raise RuntimeError("OpenCV could not open a video writer")

    def write(self, frame) -> None:
        self.writer.write(frame)

    def close(self) -> bool:
        self.writer.release()
        return True


class RecordingLibrary:
    """Clips on disk, shared by all cameras."""

    def __init__(self, directory: Path = RECORDINGS_DIR):
        self.dir = directory
        self.ffmpeg = find_ffmpeg()
        self.in_progress: set[str] = set()
        # Evidence vault hooks: a clip was saved (its path) / removed (its name, why)
        self.on_saved: Callable[[Path], None] | None = None
        self.on_removed: Callable[[str, str], None] | None = None
        if not self.ffmpeg:
            print("[rec] FFmpeg not found; clips will use OpenCV's MPEG-4 encoder")

    @property
    def encoder(self) -> str:
        return "ffmpeg (h264)" if self.ffmpeg else "opencv (mpeg-4)"

    def list(self, camera_id: str | None = None) -> list[dict]:
        out = []
        for f in self.dir.glob("*.mp4"):
            if f.name.endswith(".part.mp4"):
                continue
            meta_file = f.with_suffix(".json")
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    meta = {}
            if camera_id and meta.get("camera_id") != camera_id:
                continue
            stat = f.stat()
            out.append({
                "file": f.name,
                "size": stat.st_size,
                "started": meta.get("started") or datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
                "duration": meta.get("duration"),
                "reason": meta.get("reason", "unknown"),
                "max_level": meta.get("max_level"),
                "playable": meta.get("playable", True),
                "camera_id": meta.get("camera_id"),
                "camera": meta.get("camera"),
                "thumbnail": f.with_suffix(".jpg").exists(),
                "protected": f.with_suffix(".keep").exists(),
            })
        out.sort(key=lambda r: r["started"], reverse=True)
        return out

    def path(self, name: str, suffix: str = ".mp4") -> Path:
        clean = Path(name).name
        if not clean.endswith(".mp4") or clean.endswith(".part.mp4"):
            raise FileNotFoundError(name)
        path = self.dir / (Path(clean).stem + suffix)
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def protect(self, name: str, keep: bool) -> bool:
        """A protected clip is kept forever: retention skips it and it can't be deleted until unprotected.
        A separate marker file, so the clip's sealed details stay untouched."""
        marker = self.path(name).with_suffix(".keep")
        if keep:
            marker.write_text("kept by the owner\n", encoding="utf-8")
        else:
            marker.unlink(missing_ok=True)
        return keep

    def delete(self, name: str) -> None:
        if Path(name).name in self.in_progress:
            raise PermissionError("Recording is still in progress")
        video = self.path(name)
        if video.with_suffix(".keep").exists():
            raise PermissionError("This clip is protected. Remove the protection first.")
        for suffix in (".mp4", ".jpg", ".json"):
            video.with_suffix(suffix).unlink(missing_ok=True)
        self._removed(video.name, "deleted from the dashboard")

    def _removed(self, name: str, reason: str) -> None:
        if self.on_removed:
            self.on_removed(name, reason)

    def prune(self, retention_days: int) -> int:
        removed = 0
        if retention_days > 0:
            cutoff = (datetime.now() - timedelta(days=retention_days)).timestamp()
            for f in self.dir.glob("*.mp4"):
                if (not f.name.endswith(".part.mp4") and not f.with_suffix(".keep").exists()
                        and f.stat().st_mtime < cutoff):
                    for suffix in (".mp4", ".jpg", ".json"):
                        f.with_suffix(suffix).unlink(missing_ok=True)
                    self._removed(f.name, f"older than the {retention_days}-day retention period")
                    removed += 1
        # Leftovers from a crash mid-recording.
        for f in self.dir.glob("*.part.mp4"):
            if f.name.replace(".part.mp4", ".mp4") not in self.in_progress:
                f.unlink(missing_ok=True)
        return removed

    def usage_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.dir.iterdir() if f.is_file())


recording_library = RecordingLibrary()


class Recorder:
    """Records one camera. `source` returns that camera's latest annotated frame."""

    def __init__(self, camera_id: str, camera_name: Callable[[], str], library: RecordingLibrary = recording_library):
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.library = library
        self.dir = library.dir
        self._lock = threading.RLock()
        self._source: Callable[[], object] | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._preroll: deque = deque(maxlen=5 * FPS)
        self._writer = None
        self._size: tuple[int, int] | None = None
        self.current: dict | None = None
        self._stop_at: float | None = None
        self.postroll_seconds = 8
        self.max_clip_seconds = 300
        self.on_finished: Callable[[dict], None] | None = None
        self._no_frames_logged = 0.0

    # ---- configuration -------------------------------------------------
    def configure(self, preroll_seconds: int, postroll_seconds: int, max_clip_seconds: int) -> None:
        with self._lock:
            if self._preroll.maxlen != max(1, preroll_seconds * FPS):
                self._preroll = deque(self._preroll, maxlen=max(1, preroll_seconds * FPS))
            self.postroll_seconds = postroll_seconds
            self.max_clip_seconds = max_clip_seconds

    def start_sampler(self, source: Callable[[], object]) -> None:
        self._source = source
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True, name=f"recorder:{self.camera_id}")
        self._thread.start()

    def shutdown(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            self._finalize()

    # ---- recording control ----------------------------------------------
    @property
    def active(self) -> bool:
        return self.current is not None

    @property
    def file(self) -> str | None:
        """Name of the clip being recorded. It can be played once it is saved."""
        cur = self.current
        return cur["file"] if cur else None

    def start(self, reason: str, level: int = 0) -> str | None:
        """Start (or keep) recording. Returns the clip file name."""
        with self._lock:
            if self.current:
                self._stop_at = None  # cancel a pending stop; incident is still going
                self.current["max_level"] = max(self.current["max_level"], level)
                return self.current["file"]
            frame = self._source() if self._source else None
            if frame is None and not self._preroll:
                if time.time() - self._no_frames_logged > 30:
                    self._no_frames_logged = time.time()
                    print(f"[rec:{self.camera_id}] Cannot record: no video frames available")
                return None
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = f"{stamp}_{self.camera_id}_{reason}"
            name = f"{stem}.mp4"
            part = self.dir / f"{stem}.part.mp4"
            first = frame if frame is not None else self._preroll[-1]
            self._size = self._writer_size(first)
            # Before the file exists, so that pruning never takes it for a leftover from a crash
            self.library.in_progress.add(name)
            try:
                ffmpeg = self.library.ffmpeg
                self._writer = _FFmpegWriter(ffmpeg, part, self._size) if ffmpeg else _OpenCVWriter(part, self._size)
            except Exception as exc:
                self.library.in_progress.discard(name)
                print(f"[rec:{self.camera_id}] Could not start writer: {exc}")
                return None
            cv2.imwrite(str(self.dir / f"{stem}.jpg"), self._fit(first), [cv2.IMWRITE_JPEG_QUALITY, 85])
            preroll = list(self._preroll)
            self._preroll.clear()
            self.current = {"file": name, "part": part, "reason": reason, "max_level": level,
                            "started": time.time() - len(preroll) / FPS, "frames": 0}
            self._stop_at = None
            for f in preroll:
                self._write(f)
            if not self.current:  # writer died while flushing the pre-roll
                return None
            print(f"[rec:{self.camera_id}] Recording {name} ({len(preroll)} pre-roll frames)")
            return name

    def note_level(self, level: int) -> None:
        with self._lock:
            if self.current:
                self.current["max_level"] = max(self.current["max_level"], level)

    def stop(self, immediate: bool = False) -> None:
        with self._lock:
            if not self.current:
                return
            if immediate or self.postroll_seconds == 0:
                self._finalize()
            elif self._stop_at is None:
                self._stop_at = time.time() + self.postroll_seconds

    # ---- internals ------------------------------------------------------
    @staticmethod
    def _writer_size(frame) -> tuple[int, int]:
        h, w = frame.shape[:2]
        if w > MAX_WIDTH:
            h, w = int(h * MAX_WIDTH / w), MAX_WIDTH
        return w - w % 2, h - h % 2  # yuv420p needs even dimensions

    def _fit(self, frame):
        size = self._size or self._writer_size(frame)
        if (frame.shape[1], frame.shape[0]) != size:
            frame = cv2.resize(frame, size)
        return frame

    def _write(self, frame) -> None:
        try:
            self._writer.write(self._fit(frame))
            self.current["frames"] += 1
        except Exception as exc:
            print(f"[rec:{self.camera_id}] Write failed: {exc}")
            self._finalize()

    def _finalize(self) -> None:
        if not self.current:
            return
        cur, writer = self.current, self._writer
        self.current, self._writer, self._stop_at = None, None, None
        ok = writer.close() if writer else False
        final = self.dir / cur["file"]
        if not ok or not cur["part"].exists() or cur["frames"] == 0:
            cur["part"].unlink(missing_ok=True)
            (self.dir / f"{final.stem}.jpg").unlink(missing_ok=True)
            self.library.in_progress.discard(cur["file"])
            print(f"[rec:{self.camera_id}] Discarded {cur['file']}")
            return
        meta = {
            "reason": cur["reason"],
            "started": datetime.fromtimestamp(cur["started"]).astimezone().isoformat(),
            "duration": round(cur["frames"] / FPS, 1),
            "max_level": cur["max_level"],
            "codec": writer.codec,
            "playable": writer.playable,
            "camera_id": self.camera_id,
            "camera": self.camera_name(),
        }
        # Metadata first: a listing must never see the finished clip without its camera and reason
        (self.dir / f"{final.stem}.json").write_text(json.dumps(meta), encoding="utf-8")
        cur["part"].replace(final)
        self.library.in_progress.discard(cur["file"])
        print(f"[rec:{self.camera_id}] Saved {final.name} ({meta['duration']}s)")
        info = {**meta, "file": final.name, "path": str(final)}
        threading.Thread(target=self._after_save, args=(final, info), daemon=True).start()

    def _after_save(self, final: Path, info: dict) -> None:
        # Off the sampler thread: hashing a long clip takes a moment. Sealed before the owner gets it.
        if self.library.on_saved:
            try:
                self.library.on_saved(final)
            except Exception as exc:
                print(f"[rec:{self.camera_id}] Sealing failed: {exc}")
        if self.on_finished:
            self.on_finished(info)

    def _sample_loop(self) -> None:
        next_tick = time.time()
        while self._running.is_set():
            frame = self._source() if self._source else None
            with self._lock:
                if frame is not None:
                    if self.current:
                        self._write(frame)
                    else:
                        self._preroll.append(frame)
                elif not self.current:
                    self._preroll.clear()  # camera went away; old frames are no longer "just before"
                if self.current:
                    now = time.time()
                    if self._stop_at and now >= self._stop_at:
                        self._finalize()
                    elif now - self.current["started"] >= self.max_clip_seconds:
                        # Long incident: close this clip and continue in a new one.
                        reason, level = self.current["reason"], self.current["max_level"]
                        self._finalize()
                        self.start(reason, level)
            next_tick += 1.0 / FPS
            delay = next_tick - time.time()
            if delay < -1:
                next_tick = time.time()
            elif delay > 0:
                time.sleep(delay)

    def status(self) -> dict:
        cur = self.current
        return {
            "active": cur is not None,
            "file": cur["file"] if cur else None,
            "started": cur["started"] if cur else None,
            "stopping": self._stop_at is not None,
        }
