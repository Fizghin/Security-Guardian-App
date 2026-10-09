"""
Loud-sound alerts for one camera.

A phone camera reports the peak level of its microphone about four times a
second. The quiet end of the last minute of reports (the 20th percentile) is
what the place usually sounds like; a report far enough above that, and above an
absolute floor, is a loud sound: a door slamming, glass breaking, shouting.

Settings → Detection: "off", "log" (a SOUND event with the camera's picture) or
"alert" (while armed, also a recording and an alert to the owner). Nothing is
spoken: a sound alone doesn't say who is there. Guardian's own voice, the siren
and the owner talking through the phone are not counted.
"""
import time
from collections import deque
from typing import Callable

from services.event_service import event_service
from services.notification_service import notification_service
from services.settings_service import settings_service

WINDOW_SECONDS = 60.0
BASELINE_PERCENTILE = 20
MIN_READINGS = 20  # about 5 s of reports before anything counts as loud
FLOOR_DB = -40.0  # never loud below this
QUIET_DB = -55.0  # a place counts as at least this loud, so a silent microphone doesn't make every sound "loud"
MAX_THRESHOLD_DB = -3.0  # a sound this close to the microphone's limit always counts
COOLDOWN_SECONDS = 30.0
ALERT_REPEAT_SECONDS = 300.0
CLIP_SECONDS = 20.0


def margin_db(sensitivity: int) -> float:
    """How far above the usual level a sound must be: 46 dB at sensitivity 1, 30 dB at 5, 10 dB at 10."""
    return 50.0 - 4.0 * sensitivity


def loud_threshold(baseline: float, sensitivity: int) -> float:
    return min(MAX_THRESHOLD_DB, max(FLOOR_DB, max(baseline, QUIET_DB) + margin_db(sensitivity)))


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


class SoundMonitor:
    def __init__(self, camera_name: Callable[[], str], recorder, settings=settings_service, notifier=notification_service,
                 events=event_service, clock: Callable[[], float] = time.time):
        self.camera_name, self.recorder = camera_name, recorder
        self.settings, self.notifier, self.events, self.clock = settings, notifier, events, clock
        self.snapshot: Callable[[], bytes | None] = lambda: None
        self.own_sound: Callable[[], bool] = lambda: False  # Guardian's own voice or siren is playing
        self.incident_active: Callable[[], bool] = lambda: False
        self._readings: deque = deque()
        self._last_loud = float("-inf")
        self._last_alert = float("-inf")
        self._clip_until: float | None = None

    def baseline(self) -> float | None:
        """What the microphone usually hears, once there are enough reports."""
        if len(self._readings) < MIN_READINGS:
            return None
        return percentile([db for _, db in self._readings], BASELINE_PERCENTILE)

    def feed(self, db: float, now: float | None = None) -> bool:
        """One level report. Returns True when it was a loud sound."""
        now = self.clock() if now is None else now
        if self.own_sound():
            return False  # neither loud nor part of what the place usually sounds like
        while self._readings and now - self._readings[0][0] > WINDOW_SECONDS:
            self._readings.popleft()
        baseline = self.baseline()
        self._readings.append((now, db))
        det = self.settings.get().detection
        if (det.sound_alerts == "off" or baseline is None or db < loud_threshold(baseline, det.sound_sensitivity)
                or now - self._last_loud < COOLDOWN_SECONDS):
            return False
        self._last_loud = now
        self._loud(db, baseline, now)
        return True

    def _loud(self, db: float, baseline: float, now: float) -> None:
        cfg = self.settings.get()
        camera = self.camera_name()
        alert = cfg.detection.sound_alerts == "alert" and cfg.armed
        started = None
        if alert:
            fresh = not self.recorder.active
            clip = self.recorder.start("sound")
            if clip:
                self._clip_until = now + CLIP_SECONDS
                started = clip if fresh else None
        picture = self.snapshot()
        db, baseline = round(db), round(baseline)  # whole numbers, and never "-0"
        self.events.log("SOUND", f"Loud sound ({db} dB, usually {baseline} dB)", "MEDIUM" if alert else "LOW",
                        recording=self.recorder.file, camera=camera, snapshot=picture)
        if started:
            self.events.log("RECORDING", "Recording started", recording=started, camera=camera)
        if alert and now - self._last_alert >= ALERT_REPEAT_SECONDS:
            msg = f"{camera} heard a loud sound ({db} dB; it is usually {baseline} dB)."
            if self.notifier.send_alert(f"Loud sound at {camera}", msg, "HIGH", picture):
                self._last_alert = now
                self.events.log("ALERT", f"Owner alerted: {msg}", "HIGH", recording=self.recorder.file, camera=camera,
                                snapshot=picture)

    def tick(self, now: float | None = None) -> None:
        """Ends the clip a loud sound started, unless an incident now needs it."""
        now = self.clock() if now is None else now
        if self._clip_until is not None and now >= self._clip_until:
            self._clip_until = None
            if not self.incident_active():
                self.recorder.stop()
