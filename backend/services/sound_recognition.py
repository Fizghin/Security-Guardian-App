"""
Sound recognition on phone camera microphones.

While it is on, phone cameras stream their microphone (16 kHz PCM, about
32 kB/s) to the server. A local sound classifier (YAMNet: 521 kinds of sound,
about 4 MB, downloaded on first use into storage/models/) scores 0.975 s windows
every 0.5 s, and its classes are grouped into plain labels: glass breaking, a
smoke or fire alarm, a scream or shout, a gunshot or explosion, banging or
knocking, a dog barking, a baby crying, a siren.

A sound counts when its group's score passes a conservative threshold in 2 of 3
consecutive windows, at most once a minute per kind of sound per camera.
Windows that overlap Guardian's own voice, its siren or the owner talking don't
count.

What happens is set per kind of sound (Settings → Detection): ignore it, log it
with a picture, or also alert:
  * while armed, an alerting high-severity sound (glass, alarm, scream, gunshot)
    raises the camera to level 3 and records, even with nobody on camera;
  * other alerting sounds record a short clip and alert while armed;
  * smoke alarms and a crying baby alert even while disarmed, for safety.

One worker thread classifies for every camera; the event loop only queues
chunks. When recognition is off or can't run, the loud-sound monitor
(sound_service) covers the camera instead.
"""
import atexit
import importlib.util
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from config import MODELS_DIR
from services.event_service import event_service
from services.model_files import download
from services.notification_service import notification_service
from services.settings_service import settings_service
from services.talk_service import PCM_RATE

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite"
MODEL_FILE = "yamnet.tflite"
MODEL_MB = 4
WINDOW = 15600  # 0.975 s, what the model listens to at once
HOP = 8000  # a new window every 0.5 s
NEEDED, OUT_OF = 2, 3  # windows that must pass, of the latest ones
COOLDOWN_SECONDS = 60.0  # per kind of sound, per camera
CLIP_SECONDS = 20.0
OWN_TAIL_SECONDS = 2.0  # a phone plays a warning up to a couple of seconds after it was sent
QUEUE_CHUNKS = 300  # ~30 s of 100 ms chunks across all cameras; beyond that chunks are dropped
MISSING = "Sound recognition needs the optional mediapipe package: pip install mediapipe"


@dataclass(frozen=True)
class SoundGroup:
    label: str
    classes: tuple[str, ...]  # the classifier's own class names
    threshold: float
    severity: str


# Conservative thresholds on the classifier's 0-1 scores; with 2 of 3 windows needed, one stray window never counts.
GROUPS: dict[str, SoundGroup] = {
    "glass": SoundGroup("Glass breaking", ("Glass", "Shatter", "Breaking"), 0.25, "HIGH"),
    "alarm": SoundGroup("Smoke or fire alarm", ("Smoke detector, smoke alarm", "Fire alarm"), 0.3, "HIGH"),
    "scream": SoundGroup("Scream or shout", ("Screaming", "Shout", "Yell"), 0.5, "HIGH"),
    "gunshot": SoundGroup("Gunshot or explosion", ("Gunshot, gunfire", "Machine gun", "Fusillade", "Artillery fire",
                                                   "Explosion"), 0.4, "HIGH"),
    "banging": SoundGroup("Banging or knocking", ("Knock", "Bang", "Slam", "Thump, thud"), 0.5, "MEDIUM"),
    "dog": SoundGroup("Dog barking", ("Bark", "Dog", "Bow-wow"), 0.5, "LOW"),
    "baby": SoundGroup("Baby crying", ("Baby cry, infant cry",), 0.4, "MEDIUM"),
    "siren": SoundGroup("Siren", ("Siren", "Civil defense siren", "Police car (siren)", "Ambulance (siren)",
                                  "Fire engine, fire truck (siren)", "Emergency vehicle"), 0.5, "LOW"),
}
SAFETY = {"alarm", "baby"}  # alert even while disarmed


def group_scores(scores: dict[str, float]) -> dict[str, float]:
    """The classifier's scores per class, as the highest score in each group."""
    return {key: max((scores.get(c, 0.0) for c in g.classes), default=0.0) for key, g in GROUPS.items()}


def _mediapipe_classifier(path: Path) -> Callable[[np.ndarray], dict[str, float]]:
    from mediapipe.tasks.python import audio
    from mediapipe.tasks.python.components.containers import audio_data
    from mediapipe.tasks.python.core import base_options

    options = audio.AudioClassifierOptions(base_options=base_options.BaseOptions(model_asset_path=str(path)),
                                           max_results=-1, running_mode=audio.RunningMode.AUDIO_CLIPS)
    classifier = audio.AudioClassifier.create_from_options(options)
    atexit.register(classifier.close)  # closing it while the interpreter tears down prints an error

    def classify(samples: np.ndarray) -> dict[str, float]:
        results = classifier.classify(audio_data.AudioData.create_from_array(samples, PCM_RATE))
        if not results or not results[0].classifications:
            return {}
        return {c.category_name: c.score for c in results[0].classifications[0].categories}

    return classify


_PREPARE = object()


class SoundModel:
    """The classifier and the one worker thread that runs it for every camera."""

    def __init__(self, models_dir: Path = MODELS_DIR, url: str = MODEL_URL, create=_mediapipe_classifier,
                 installed: bool | None = None):
        self.path = models_dir / MODEL_FILE
        self.url = url
        self._create = create
        self.installed = importlib.util.find_spec("mediapipe") is not None if installed is None else installed
        self.state = "idle"  # idle | downloading | loading | ready | error
        self.progress: float | None = None
        self.error: str | None = None
        self._classify: Callable[[np.ndarray], dict[str, float]] | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_CHUNKS)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._classify is not None

    def prepare(self, retry: bool = False) -> bool:
        """Downloads (if needed) and loads the model on the worker, the first time it is needed.
        After a failure it only tries again when asked (retry). Returns False if it can't start."""
        with self._lock:
            if not self.installed:
                return False
            if self.state in ("downloading", "loading", "ready") or (self.state == "error" and not retry):
                return self.state != "error"
            self.state = "loading" if self.path.exists() else "downloading"
            self.error, self.progress = None, None
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="sound-recognition")
                self._thread.start()
        self._queue.put(_PREPARE)
        return True

    def submit(self, recognizer: "SoundRecognizer", chunk: bytes, own: bool) -> None:
        """Queues a chunk of a camera's microphone. Never blocks: when the worker falls behind, chunks are dropped."""
        if self._classify is None:
            return
        try:
            self._queue.put_nowait((recognizer, chunk, own))
        except queue.Full:
            pass

    def classify(self, samples: np.ndarray) -> dict[str, float]:
        return self._classify(samples) if self._classify else {}

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _PREPARE:
                self._load()
                continue
            recognizer, chunk, own = item
            try:
                recognizer.process(chunk, own)
            except Exception as exc:  # keep serving the other cameras
                print(f"[sounds] Recognition failed: {exc}")

    def _load(self) -> None:
        if self._classify is not None:
            return
        try:
            if not self.path.exists():
                print("[sounds] Downloading the sound recognition model")
                download(self.url, self.path, self._progress)
            self.state, self.progress = "loading", None
            self._classify = self._create(self.path)
            self.state = "ready"
            print("[sounds] Sound recognition ready")
        except Exception as exc:
            action = "load" if self.path.exists() else "download"
            self.state = "error"
            self.error = (f"Could not {action} the sound model: {exc}. To set it up offline, save {self.url} "
                          f"as {self.path}.")
            print(f"[sounds] {self.error}")

    def _progress(self, fraction: float | None) -> None:
        self.progress = fraction

    def status(self) -> dict:
        return {"installed": self.installed, "state": self.state if self.installed else "missing",
                "downloaded": self.path.exists(), "progress": self.progress,
                "error": self.error if self.installed else MISSING, "size_mb": MODEL_MB}


sound_model = SoundModel()


class SoundRecognizer:
    """Recognised sounds for one camera."""

    def __init__(self, camera_name: Callable[[], str], relay, brain, recorder, settings=settings_service,
                 notifier=notification_service, events=event_service, model: SoundModel = sound_model,
                 clock: Callable[[], float] = time.time):
        self.camera_name, self.relay, self.brain, self.recorder = camera_name, relay, brain, recorder
        self.settings, self.notifier, self.events, self.model, self.clock = settings, notifier, events, model, clock
        self.snapshot: Callable[[], bytes | None] = lambda: None
        self.own_sound: Callable[[], bool] = lambda: False  # Guardian's own voice or siren is playing
        self.active = False  # the phone is asked for its microphone for recognition
        self._own_until = 0.0
        # Worker thread only:
        self._samples = np.empty(0, np.int16)
        self._received = 0  # samples received so far
        self._own_end = -1  # where the latest chunk with Guardian's own sound ended
        self._scores: dict[str, deque] = {key: deque(maxlen=OUT_OF) for key in GROUPS}
        self._last: dict[str, float] = {}
        self._clip_until: float | None = None

    def tick(self, now: float | None = None) -> bool:
        """From the camera loop: asks the phone for its microphone while recognition is on and can run, and ends
        the clip a sound started. Returns True while recognition covers this camera (audio is arriving)."""
        now = self.clock() if now is None else now
        on = self.settings.get().detection.sound_recognition and self.relay.mic_live(now)
        if on and not self.model.ready:
            self.model.prepare()  # the first use downloads the model
        self.active = on and self.model.ready
        self.relay.want("sounds", self.active)
        if self._clip_until is not None and now >= self._clip_until:
            self._clip_until = None
            if not self.brain.incident_active:
                self.recorder.stop()
        return self.active and self.relay.streaming(now)

    def feed(self, chunk: bytes) -> None:
        """A piece of the microphone, on the event loop: only queued here."""
        if not self.active:
            return
        now = self.clock()
        if self.own_sound():
            self._own_until = now + OWN_TAIL_SECONDS
        self.model.submit(self, chunk, now < self._own_until)

    def process(self, chunk: bytes, own: bool = False) -> list[str]:
        """On the worker: adds a chunk and judges each complete window. Returns the kinds of sound recognised."""
        samples = np.frombuffer(chunk, np.int16)
        self._received += len(samples)
        if own:
            self._own_end = self._received
        self._samples = np.concatenate((self._samples, samples))
        found = []
        while len(self._samples) >= WINDOW:
            start = self._received - len(self._samples)
            window = self._samples[:WINDOW].astype(np.float32) / 32768
            self._samples = self._samples[HOP:]
            found += self.judge(group_scores(self.model.classify(window)), own=self._own_end > start)
        return found

    def judge(self, scores: dict[str, float], own: bool = False, now: float | None = None) -> list[str]:
        """One window's score per group. A group counts when NEEDED of the latest OUT_OF windows pass its threshold."""
        now = self.clock() if now is None else now
        actions = self.settings.get().detection.sound_actions
        found = []
        for key, group in GROUPS.items():
            history = self._scores[key]
            history.append(0.0 if own else scores.get(key, 0.0))
            passed = [s for s in history if s >= group.threshold]
            if getattr(actions, key) == "off" or len(passed) < NEEDED:
                continue
            if now - self._last.get(key, float("-inf")) < COOLDOWN_SECONDS:
                continue
            self._last[key] = now
            history.clear()
            found.append(key)
            self._heard(key, max(passed), now)
        return found

    def _heard(self, key: str, score: float, now: float) -> None:
        cfg = self.settings.get()
        group = GROUPS[key]
        camera = self.camera_name()
        alerting = getattr(cfg.detection.sound_actions, key) == "alert"
        raised = started = None
        if alerting and cfg.armed:
            if group.severity == "HIGH":
                raised = self.brain.sound_alarm(group.label)  # level 3 and a recording; the alert follows the levels
            if not raised:
                fresh = not self.recorder.active
                clip = self.recorder.start("sound")
                if clip:
                    if not self.brain.incident_active:
                        self._clip_until = now + CLIP_SECONDS
                    started = clip if fresh else None
        picture = self.snapshot()
        print(f"[sounds] {group.label} on {camera} (score {score:.2f})")
        self.events.log("SOUND", f"{group.label} heard on {camera} (score {score:.2f})", group.severity,
                        recording=self.recorder.file, camera=camera, snapshot=picture)
        if started:
            self.events.log("RECORDING", "Recording started", recording=started, camera=camera)
        if alerting and not raised and (cfg.armed or key in SAFETY):
            msg = f"{group.label} heard on {camera}."
            if not cfg.armed:
                msg += " Guardian is disarmed; this alert is for your safety."
            if self.notifier.send_alert(f"{group.label} at {camera}", msg, group.severity, picture):
                self.events.log("ALERT", f"Owner alerted: {msg}", group.severity, recording=self.recorder.file,
                                camera=camera, snapshot=picture)
