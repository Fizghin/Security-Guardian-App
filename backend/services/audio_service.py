"""
Audio routing.

ServerAudio is this computer's speaker, shared by every camera: the siren stays
on while any camera wants it, and the same line requested by several cameras
at once (e.g. during a panic) is only spoken once.

CameraSpeaker sends a camera's warnings to the places configured for it: this
computer, the phone the camera runs on, or both. While someone talks through
the camera (talk_service), its warnings wait.
"""
import threading
import time
from typing import Callable

from services.siren_service import siren_service
from services.tts_service import tts_service

DEDUPE_SECONDS = 8


def speech_seconds(text: str, rate: int) -> float:
    """Roughly how long a line takes to say at `rate` words per minute."""
    return len(text.split()) * 60 / max(rate, 1) + 1.0


class ServerAudio:
    def __init__(self, tts=tts_service, siren=siren_service):
        self.tts, self.siren = tts, siren
        self._lock = threading.Lock()
        self._owners: dict[str, float] = {}
        self._recent: dict[str, float] = {}

    def say(self, text: str, rate: int, interrupt: bool = False) -> bool:
        now = time.time()
        with self._lock:
            self._recent = {t: ts for t, ts in self._recent.items() if now - ts < DEDUPE_SECONDS}
            if text in self._recent:
                return True
            self._recent[text] = now
        return self.tts.say(text, rate, interrupt=interrupt)

    def siren_on(self, owner: str, max_seconds: int) -> bool:
        with self._lock:
            self._owners[owner] = time.time() + max_seconds
        return self.siren.start(max_seconds)

    def siren_off(self, owner: str) -> None:
        with self._lock:
            self._owners.pop(owner, None)
            now = time.time()
            still_wanted = any(deadline > now for deadline in self._owners.values())
        if not still_wanted:
            self.siren.stop()


server_audio = ServerAudio()


class CameraSpeaker:
    def __init__(self, camera_id: str, outputs: Callable[[], str], phone_send: Callable[[dict], bool] | None = None,
                 server: ServerAudio = server_audio, paused: Callable[[], bool] = lambda: False):
        self.camera_id = camera_id
        self._outputs = outputs  # "server" | "device" | "both"
        self._phone_send = phone_send
        self.server = server
        self.paused = paused  # someone is talking through this camera
        self._siren_requested = False
        self._phone_siren_until = 0.0
        self._speaking_until = 0.0

    def _targets(self) -> tuple[bool, bool]:
        out = self._outputs()
        return out in ("server", "both"), out in ("device", "both") and self._phone_send is not None

    def say(self, text: str, rate: int, interrupt: bool = False) -> bool:
        if self.paused():
            return False
        server, device = self._targets()
        spoken = False
        if device:
            # Browsers count speaking rate relative to 1.0 = normal (~165 words per minute).
            spoken = self._phone_send({"type": "speak", "text": text, "rate": round(rate / 165, 2)}) or spoken
        if server:
            spoken = self.server.say(text, rate, interrupt) or spoken
        if spoken:
            self._speaking_until = time.time() + speech_seconds(text, rate)
        return spoken

    @property
    def sounding(self) -> bool:
        """A warning or the siren is playing, which this camera's microphone hears too."""
        return self.siren_active or time.time() < self._speaking_until

    @property
    def siren_active(self) -> bool:
        """True while a siren this camera asked for is actually sounding."""
        if not self._siren_requested:
            return False
        server, device = self._targets()
        return (server and self.server.siren.active) or (device and time.time() < self._phone_siren_until)

    def siren(self, on: bool, max_seconds: int = 60) -> bool:
        server, device = self._targets()
        ok = False
        if device:
            sent = self._phone_send({"type": "siren", "on": on, "seconds": max_seconds})
            self._phone_siren_until = time.time() + max_seconds if on and sent else 0.0
            ok = sent or ok
        if server:
            if on:
                ok = self.server.siren_on(self.camera_id, max_seconds) or ok
            else:
                self.server.siren_off(self.camera_id)
        self._siren_requested = on and ok
        return ok

    def describe(self) -> str:
        server, device = self._targets()
        return " and ".join(x for x, on in (("this computer", server), ("the phone", device)) if on) or "nowhere"
