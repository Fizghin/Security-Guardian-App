"""
Live audio between the dashboard and the cameras.

Talk: the owner holds a button on the Live page and their voice goes where the
camera's warnings go (its audio setting): to the phone over its audio link, to
this computer's speakers, or both. One person talks through a camera at a time,
and that camera's voice warnings wait meanwhile.

Listen: a phone sends its microphone only while at least one dashboard listens,
and the server passes it on to all of them.

Analysis: the server can also ask for the microphone for its own use, to
recognise sounds and, during an intrusion, to hear what the person says (the
Guard Bot). The phone then sends it whether or not anyone listens.

The phone also reports its microphone level about four times a second; the
camera's SoundMonitor takes those readings from here.

Audio is 16-bit mono PCM at 16 kHz. The relays live on the server's event loop
(the dashboard and the phone port share it); the speaker on this computer is fed
from a thread so a slow player never holds that loop up.
"""
import asyncio
import os
import platform
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from typing import Callable

from config import DATA_DIR

PCM_RATE = 16000
MAX_CHUNK_BYTES = PCM_RATE * 2  # one second; senders use ~100 ms chunks
PHONE_BACKLOG = 10  # voice chunks (~1 s) waiting for the phone before newer ones are dropped
LISTEN_BACKLOG = 20  # phone chunks (~2 s) waiting for a slow dashboard; the oldest are dropped
MIC_FRESH_SECONDS = 2.0  # a phone that sent no level for this long has no live microphone
TALK_ECHO_SECONDS = 1.5  # the phone is still playing the end of what was said
MAX_WAV_SECONDS = 60  # longest talk kept for players that only play files


@dataclass(frozen=True)
class TalkPlayer:
    name: str
    mode: str  # "live": plays PCM from stdin as the owner talks; "after": plays a WAV once they let go
    command: tuple[str, ...] = ()


def find_talk_player(system: str | None = None, which=shutil.which) -> TalkPlayer | None:
    """How this computer plays the owner's voice. Live where a player reads raw PCM from stdin;
    otherwise the speech is collected and played as a WAV when the talker lets go."""
    system = system or platform.system()
    rate = str(PCM_RATE)
    if which("paplay"):
        return TalkPlayer("paplay", "live", ("paplay", "--raw", "--format=s16le", f"--rate={rate}", "--channels=1"))
    if which("aplay"):
        return TalkPlayer("aplay", "live", ("aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", rate, "-c", "1"))
    if which("ffplay"):
        return TalkPlayer("ffplay", "live", ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-fflags", "nobuffer",
                                             "-f", "s16le", "-ar", rate, "-ac", "1", "-i", "-"))
    if system == "Darwin" and which("afplay"):
        return TalkPlayer("afplay", "after", ("afplay",))
    if system == "Windows":
        return TalkPlayer("winsound", "after")
    return None


talk_player = find_talk_player()


def talk_player_status() -> dict:
    return {"mode": talk_player.mode if talk_player else None, "player": talk_player.name if talk_player else None}


class TalkError(Exception):
    def __init__(self, message: str, busy: bool = False):
        super().__init__(message)
        self.busy = busy


class _LiveSpeaker:
    """This computer's speakers, playing the voice as it arrives."""

    def __init__(self, player: TalkPlayer, popen=subprocess.Popen):
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._proc = popen(list(player.command), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        self._name = player.name
        threading.Thread(target=self._run, daemon=True, name="talk-player").start()

    def feed(self, chunk: bytes) -> None:
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            pass  # the player fell behind; dropping keeps the delay short

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            threading.Thread(target=self._queue.put, args=(None,), daemon=True).start()

    def _run(self) -> None:
        stdin = self._proc.stdin
        while (chunk := self._queue.get()) is not None:
            try:
                stdin.write(chunk)
                stdin.flush()
            except (OSError, ValueError):  # the player quit, e.g. no sound device
                print(f"[talk] {self._name} stopped playing (exit code {self._proc.poll()})")
                while self._queue.get() is not None:
                    pass
                break
        try:
            stdin.close()
            self._proc.wait(timeout=MAX_WAV_SECONDS)  # it plays what it has buffered, then exits
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._proc.kill()


class _ClipSpeaker:
    """This computer's speakers, for players that only play files: the voice plays when the talker lets go."""

    def __init__(self, player: TalkPlayer, play=None):
        self._player = player
        self._play = play or _play_wav
        self._chunks: list[bytes] = []
        self._bytes = 0

    def feed(self, chunk: bytes) -> None:
        if self._bytes + len(chunk) <= MAX_WAV_SECONDS * PCM_RATE * 2:
            self._chunks.append(chunk)
            self._bytes += len(chunk)

    def close(self) -> None:
        if self._chunks:
            threading.Thread(target=self._play, args=(self._player, b"".join(self._chunks)), daemon=True,
                             name="talk-player").start()


def _play_wav(player: TalkPlayer, pcm: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix="talk-", suffix=".wav", dir=DATA_DIR)
    os.close(fd)
    try:
        with wave.open(name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(PCM_RATE)
            w.writeframes(pcm)
        if player.name == "winsound":
            import winsound
            winsound.PlaySound(name, winsound.SND_FILENAME)
        else:
            subprocess.run([*player.command, name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=MAX_WAV_SECONDS + 30)
    except Exception as exc:
        print(f"[talk] Could not play the recorded voice: {exc}")
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


class PhoneAudio:
    """A phone's audio connection. Everything for it goes through one queue, so only one task writes to its socket."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    def send(self, message: bytes | dict) -> bool:
        if isinstance(message, bytes) and self.queue.qsize() >= PHONE_BACKLOG:
            return False  # the phone can't keep up; a gap is better than a growing delay
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            return False

    def close(self) -> None:
        """Ends the connection, e.g. because the same phone page connected again."""
        while True:
            try:
                self.queue.put_nowait(None)
                return
            except asyncio.QueueFull:
                self.queue.get_nowait()


class Talk:
    """One person talking through a camera, from pressing the button until letting go."""

    def __init__(self, relay: "AudioRelay", to_phone: bool, player: TalkPlayer | None, popen=subprocess.Popen,
                 play=None):
        self.relay = relay
        self.to_phone = to_phone
        self.player = player
        self.speaker = None
        if player is not None:
            self.speaker = _LiveSpeaker(player, popen) if player.mode == "live" else _ClipSpeaker(player, play)
        self.samples = 0

    def describe(self) -> dict:
        """Where the voice goes: the phone (as it is spoken) and this computer ("live" or "after" letting go)."""
        return {"phone": self.to_phone, "computer": self.player.mode if self.player else None}

    @property
    def seconds(self) -> float:
        return self.samples / PCM_RATE

    def feed(self, chunk: bytes) -> None:
        if not chunk or len(chunk) > MAX_CHUNK_BYTES or len(chunk) % 2:
            return
        self.samples += len(chunk) // 2
        if self.to_phone:
            self.relay.send_to_phone(chunk)
        if self.speaker:
            self.speaker.feed(chunk)

    def close(self) -> None:
        if self.speaker:
            self.speaker.close()


class AudioRelay:
    """Live audio for one camera: its phone's audio link, the person talking, and the people listening."""

    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self.phone: PhoneAudio | None = None
        self.talk: Talk | None = None
        self.talk_ended = 0.0
        self.listeners: list[asyncio.Queue] = []
        self.level_db: float | None = None
        self.level_time = 0.0
        self._levels: deque = deque(maxlen=400)  # (time, dB) not yet seen by the sound monitor
        self.analysis: frozenset[str] = frozenset()  # what the server wants the microphone for: "sounds", "speech"
        self.analyse: Callable[[bytes], None] | None = None  # gets the microphone while analysis is wanted
        self.audio_time = 0.0  # when the phone last sent microphone audio
        self._loop: asyncio.AbstractEventLoop | None = None  # the loop the phone's connection lives on

    # ---- the phone ------------------------------------------------------------------
    def connect_phone(self) -> PhoneAudio:
        old, self.phone = self.phone, PhoneAudio()
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        if old is not None:
            old.close()  # the page was opened again, or reconnected before the old socket noticed
        self.phone.send({"type": "listen", "on": bool(self.listeners)})
        if self.analysis:
            self.phone.send(self._analysis_message())
        return self.phone

    def disconnect_phone(self, phone: PhoneAudio) -> None:
        if self.phone is phone:
            self.phone = None

    def send_to_phone(self, message: bytes | dict) -> bool:
        return self.phone.send(message) if self.phone is not None else False

    def phone_level(self, db: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.level_db, self.level_time = db, now
        self._levels.append((now, db))

    def phone_audio(self, chunk: bytes) -> None:
        if not chunk or len(chunk) > MAX_CHUNK_BYTES or len(chunk) % 2:
            return
        self.audio_time = time.time()
        for q in self.listeners:
            if q.full():
                q.get_nowait()
            q.put_nowait(chunk)
        analyse = self.analyse
        if self.analysis and analyse is not None:
            analyse(chunk)

    def take_levels(self) -> list[tuple[float, float]]:
        out = []
        while self._levels:
            out.append(self._levels.popleft())
        return out

    def mic_live(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) - self.level_time < MIC_FRESH_SECONDS

    # ---- talk ---------------------------------------------------------------------------
    @property
    def talking(self) -> bool:
        return self.talk is not None

    def talked_recently(self, now: float | None = None) -> bool:
        """Someone talks, or just did and the phone may still be playing it."""
        now = time.time() if now is None else now
        return self.talk is not None or now - self.talk_ended < TALK_ECHO_SECONDS

    def start_talk(self, output: str, player: TalkPlayer | None = None, popen=subprocess.Popen, play=None) -> Talk:
        """output is the camera's audio setting: "server", "device" (its phone) or "both"."""
        if self.talk is not None:
            raise TalkError("Someone else is talking through this camera right now", busy=True)
        wants_phone, wants_computer = output in ("device", "both"), output in ("server", "both")
        to_phone = wants_phone and self.phone is not None
        player = player if wants_computer else None
        if not to_phone and player is None:
            problems = []
            if wants_phone:
                problems.append("the phone is not connected for audio (open its camera page and tap Start camera)")
            if wants_computer:
                problems.append("this computer has no audio player (install pulseaudio-utils, alsa-utils or ffmpeg)")
            raise TalkError("Your voice can't be played: " + " and ".join(problems))
        try:
            self.talk = Talk(self, to_phone, player, popen, play)
        except OSError as exc:  # the player could not be started
            if not to_phone:
                raise TalkError(f"Could not start {player.name}: {exc}")
            self.talk = Talk(self, to_phone, None)
        return self.talk

    def end_talk(self, talk: Talk) -> float:
        """Returns how many seconds of voice were sent."""
        if self.talk is talk:
            self.talk = None
            self.talk_ended = time.time()
        talk.close()
        return talk.seconds

    # ---- listen -------------------------------------------------------------------------
    def add_listener(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=LISTEN_BACKLOG)
        self.listeners.append(q)
        if len(self.listeners) == 1:
            self.send_to_phone({"type": "listen", "on": True})
        return q

    def remove_listener(self, q: asyncio.Queue) -> None:
        if q in self.listeners:
            self.listeners.remove(q)
            if not self.listeners:
                self.send_to_phone({"type": "listen", "on": False})

    # ---- analysis -----------------------------------------------------------------------
    def want(self, purpose: str, on: bool) -> None:
        """Asks the phone for its microphone for the server's own use ("sounds" or "speech"), or stops
        asking. The phone sends it while anything wants it: about 32 kB/s. Safe to call from any thread."""
        if on == (purpose in self.analysis):
            return
        self.analysis = self.analysis | {purpose} if on else self.analysis - {purpose}
        message = self._analysis_message()
        loop = self._loop
        try:
            if loop is None or asyncio.get_running_loop() is loop:
                self.send_to_phone(message)
                return
        except RuntimeError:
            pass  # not on any loop: a camera thread
        try:
            loop.call_soon_threadsafe(self.send_to_phone, message)
        except RuntimeError:
            pass  # the server is shutting down

    def _analysis_message(self) -> dict:
        return {"type": "analyse", "sounds": "sounds" in self.analysis, "speech": "speech" in self.analysis}

    def streaming(self, now: float | None = None) -> bool:
        """The phone is sending its microphone, not just levels."""
        return (time.time() if now is None else now) - self.audio_time < MIC_FRESH_SECONDS

    # ---- reporting ----------------------------------------------------------------------
    def status(self, now: float | None = None) -> dict:
        mic = self.mic_live(now)
        return {"link": self.phone is not None, "mic": mic, "level_db": self.level_db if mic else None,
                "talking": self.talk is not None, "listeners": len(self.listeners)}


class AudioHub:
    def __init__(self):
        self._relays: dict[str, AudioRelay] = {}
        self._lock = threading.Lock()

    def relay(self, camera_id: str) -> AudioRelay:
        with self._lock:
            relay = self._relays.get(camera_id)
            if relay is None:
                relay = self._relays[camera_id] = AudioRelay(camera_id)
            return relay


audio_hub = AudioHub()
