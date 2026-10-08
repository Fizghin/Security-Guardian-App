"""
Alarm siren on the server's speakers.

Windows plays through winsound; macOS through afplay; Linux through
paplay/aplay/ffplay, whichever is installed.
"""
import platform
import shutil
import subprocess
import threading
import time
import wave

import numpy as np

from config import DATA_DIR

SIREN_FILE = DATA_DIR / "siren.wav"
IS_WINDOWS = platform.system() == "Windows"


def _write_siren_wav(path, seconds=2.0, rate=22050):
    """Two-tone wail: a 600 Hz -> 1400 Hz sweep up and back down."""
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    sweep = 600 + 800 * (1 - np.abs(2 * (t / seconds) - 1))
    phase = 2 * np.pi * np.cumsum(sweep) / rate
    samples = (np.sign(np.sin(phase)) * 0.35 + np.sin(phase) * 0.35) * 32767
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())


def _player_command():
    if platform.system() == "Darwin" and shutil.which("afplay"):
        return ["afplay", str(SIREN_FILE)]
    if shutil.which("paplay"):
        return ["paplay", str(SIREN_FILE)]
    if shutil.which("aplay"):
        return ["aplay", "-q", str(SIREN_FILE)]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(SIREN_FILE)]
    return None


class SirenService:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.player = "winsound" if IS_WINDOWS else (_player_command() or [None])[0]
        self.available = self.player is not None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, max_seconds: int = 60) -> bool:
        if self.active:
            return True
        if not self.available:
            print("[siren] No audio player found (install pulseaudio-utils or alsa-utils)")
            return False
        if not SIREN_FILE.exists():
            _write_siren_wav(SIREN_FILE)
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, args=(max_seconds,), daemon=True, name="siren")
        self._thread.start()
        print(f"[siren] ON (max {max_seconds}s)")
        return True

    def _run(self, max_seconds: int) -> None:
        deadline = time.time() + max_seconds
        try:
            if IS_WINDOWS:
                import winsound
                winsound.PlaySound(str(SIREN_FILE), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
                self._stop.wait(max(0.0, deadline - time.time()))
                winsound.PlaySound(None, winsound.SND_PURGE)
                return
            cmd = _player_command()
            while not self._stop.is_set() and time.time() < deadline and cmd:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while self._proc.poll() is None:
                    if self._stop.wait(0.1) or time.time() >= deadline:
                        self._proc.terminate()
                        try:
                            self._proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            self._proc.kill()
                        break
                if not self._stop.is_set() and self._proc.returncode not in (0, None, -15):
                    print(f"[siren] Player exited with {self._proc.returncode}")
                    self._stop.wait(1.0)
        except Exception as exc:
            print(f"[siren] Error: {exc}")
        finally:
            self._proc = None
            self.started_at = None
            print("[siren] OFF")

    def stop(self) -> None:
        if not self.active:
            return
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
        if self._thread:
            self._thread.join(timeout=3)

    def status(self) -> dict:
        return {"active": self.active, "available": self.available, "player": self.player,
                "started_at": self.started_at}


siren_service = SirenService()
