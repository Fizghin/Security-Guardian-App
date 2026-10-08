import json
import queue
import subprocess
import sys
import threading

from config import BACKEND_DIR

SPEAK_SCRIPT = BACKEND_DIR / "scripts" / "speak.py"


class TTSService:
    """Speaks lines one at a time on the server's speakers."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self.available: bool | None = None  # None = not checked yet
        self.engine: str | None = None
        self.error: str | None = None
        self.speaking = False

    def check(self) -> None:
        try:
            out = subprocess.run([sys.executable, str(SPEAK_SCRIPT), "--check"],
                                 capture_output=True, text=True, timeout=30)
            info = json.loads(out.stdout.strip().splitlines()[-1])
            self.available, self.engine, self.error = info["ok"], info["engine"], info["error"]
        except Exception as exc:
            self.available, self.engine, self.error = False, None, str(exc)
        if self.available:
            print(f"[tts] Speech engine: {self.engine}")
        else:
            print(f"[tts] No speech engine available: {self.error}")

    def check_async(self) -> None:
        threading.Thread(target=self.check, daemon=True, name="tts-check").start()

    def say(self, text: str, rate: int = 165, interrupt: bool = False) -> bool:
        if not text or self.available is False:
            return False
        with self._lock:
            if interrupt or self._queue.qsize() >= 2:
                # Old warnings are stale by the time they would be spoken.
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                        self._queue.task_done()
                    except queue.Empty:
                        break
            self._queue.put((text, rate))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, daemon=True, name="tts")
                self._worker.start()
        return True

    def _run(self) -> None:
        while True:
            text, rate = self._queue.get()
            self.speaking = True
            try:
                proc = subprocess.run([sys.executable, str(SPEAK_SCRIPT), "--rate", str(rate), text],
                                      capture_output=True, text=True, timeout=90)
                if proc.returncode != 0:
                    self.error = (proc.stderr or "").strip().removeprefix("TTS error: ")[-300:] or f"exit code {proc.returncode}"
                    print(f"[tts] {self.error}")
                else:
                    self.error = None
            except Exception as exc:
                self.error = str(exc)
                print(f"[tts] {exc}")
            finally:
                self.speaking = False
                self._queue.task_done()

    def status(self) -> dict:
        return {"available": self.available, "engine": self.engine, "error": self.error,
                "speaking": self.speaking, "queued": self._queue.qsize()}


tts_service = TTSService()
