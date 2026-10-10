"""
Threat escalation for one camera.

  0 Clear       nobody unrecognised in view
  1 Detected    unrecognised person appears          -> voice greeting
  2 Loitering   still there after level2_after secs  -> voice warning (+ recording by default)
  3 Intruder    still there after level3_after secs  -> owner alerted
  4 Alarm       still there after level4_after secs  -> siren

People the tracker is still identifying ("pending") do not start an incident,
so a resident walking up to the camera is recognised before anything is said.
Strangers who are remembered visitors are noted in the log and in the alert.
A stranger at a time the camera is usually quiet (its learned routine) is noted
as unusual, and the owner can be alerted about it straight away.
An incident ends when nobody unrecognised has been seen for clear_after
seconds, when the system is disarmed, or when the alarm is reset. A panic is
driven by the CameraManager and stays at level 4 until it is reset.
"""
import threading
import time
from typing import Callable, List

from data.fallback_messages import greeting
from models.domain import Detection
from services.ai_service import WarningContext, ai_service
from services.event_service import event_service
from services.notification_service import notification_service
from services.routine_service import Judgement
from services.settings_service import settings_service

LEVEL_NAMES = {0: "Clear", 1: "Person detected", 2: "Loitering", 3: "Intruder", 4: "Alarm"}
SEVERITY = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}
# Events that keep a picture of the moment for the event log
PICTURE_EVENTS = {"INSIDER", "DETECTION", "ESCALATION", "ALERT", "VISITOR"}
ALERT_REPEAT_SECONDS = 120
INSIDER_LOG_SECONDS = 300
PRESENCE_SECONDS = 3  # no voice warnings once nobody has been seen for this long
UNUSUAL_ALERT_SECONDS = 600  # alerts about strangers at unusual times, per camera


def voice_cooldown(persistence: int) -> float:
    """Seconds between spoken warnings: 40 s at persistence 0, 8 s at 100."""
    return 40.0 - 0.32 * persistence


class CameraBrain:
    def __init__(self, camera_id: str, camera_name: Callable[[], str], speaker, recorder,
                 settings=settings_service, ai=ai_service, notifier=notification_service, events=event_service,
                 prune: Callable[[int], int] = lambda days: 0, clock: Callable[[], float] = time.time):
        self.camera_id, self.camera_name = camera_id, camera_name
        self.speaker, self.recorder = speaker, recorder
        self.settings, self.ai, self.notifier, self.events = settings, ai, notifier, events
        self.prune, self.clock = prune, clock
        self.snapshot: Callable[[], bytes | None] = lambda: None
        self.routine: Callable[[float], Judgement | None] = lambda now: None  # the camera's learned routine
        self._lock = threading.RLock()

        self.threat_level = 0
        self.manual = False
        self.simulated = False
        self.incident_start: float | None = None
        self.last_seen: float | None = None
        self._incident_id = 0
        self._peak = 0
        self._spoken_level = 0
        self._siren_fired = False
        self._alerted = False
        self.last_ai_time = 0.0
        self.last_alert_time = 0.0
        self.said: List[str] = []
        self.picture_event: int | None = None  # the incident's latest event with a picture
        self._seen_before: dict[int, str] = {}  # visitor id -> "seen before" note, this incident
        self.unusual = False  # this incident started at a time the camera is usually quiet
        self._unusual_alert_time: float | None = None

        self.persons = 0
        self.pending = 0
        self.insiders_in_view: List[str] = []
        self._insider_seen: dict[str, float] = {}
        self._insider_logged: dict[str, float] = {}
        self._greeted: dict[str, float] = {}

        self.last_message: str | None = None
        self.last_message_time: float | None = None
        self.last_message_source: str | None = None

        recorder.on_finished = self._on_recording_finished

    @property
    def armed(self) -> bool:
        return self.settings.get().armed

    @property
    def incident_active(self) -> bool:
        return self.incident_start is not None

    def _log(self, event_type: str, description: str, severity: str = "INFO", recording: str | None = None) -> None:
        picture = None
        if event_type in PICTURE_EVENTS:
            picture = self.snapshot()
            recording = recording or self.recorder.file  # the clip that shows this moment
        event_id = self.events.log(event_type, description, severity, recording=recording, camera=self.camera_name(),
                                   snapshot=picture)
        if picture and event_id is not None and self.incident_start is not None:
            self.picture_event = event_id

    # ---- inputs ---------------------------------------------------------
    def process(self, detections: List[Detection], now: float | None = None) -> None:
        now = self.clock() if now is None else now
        cfg = self.settings.get()
        with self._lock:
            persons = [d for d in detections if d.class_name == "person"]
            known = [d for d in persons if d.status == "known"]
            unknown = [d for d in persons if d.status == "unknown"]
            pending = [d for d in persons if d.status == "pending"]
            self.persons, self.pending = len(persons), len(pending)
            self.insiders_in_view = sorted({d.identity for d in known if d.identity})

            for name in self.insiders_in_view:
                self._insider_seen[name] = now
                if name not in self._insider_logged or now - self._insider_logged[name] >= INSIDER_LOG_SECONDS:
                    self._insider_logged[name] = now
                    self._log("INSIDER", f"Recognised {name}")
                self._maybe_greet(name, now)

            real_unknown = [d for d in unknown if not d.simulated]
            if real_unknown:
                # An insider who turns away from the camera should not trigger an alarm.
                grace = cfg.detection.insider_grace_seconds
                recent = [n for n, t in self._insider_seen.items() if now - t <= grace]
                if recent and not any(d.face_visible for d in real_unknown) and len(persons) <= len(recent):
                    unknown = [d for d in unknown if d.simulated]

            quiet = None
            if unknown and cfg.armed:
                if self.incident_start is None:
                    self._begin_incident(now, simulated=all(d.simulated for d in unknown))
                    who = "Unrecognised person" if len(unknown) == 1 else f"{len(unknown)} unrecognised people"
                    seen = self._returning(unknown)
                    quiet = self._unusual_time(now, cfg.learning.unusual_activity)
                    self._log_level("DETECTION", f"{who} detected" + (" (test)" if self.simulated else "")
                                    + (f" ({'; '.join(seen)})" if seen else "")
                                    + (f". Unusual: {quiet}." if quiet else ""), "MEDIUM" if quiet else "LOW")
                elif seen := self._returning(unknown):
                    # Recognised after the incident started, e.g. once they faced the camera
                    self._log("VISITOR", f"Returning visitor: {'; '.join(seen)}", "LOW")
                self.last_seen = now
            elif pending and self.incident_start is not None:
                self.last_seen = now  # someone is still there while we work out who they are
            self._update(now)
            if quiet and cfg.learning.unusual_activity == "alert":
                self._alert_unusual(now, quiet)  # after the first warning, so a prepared line still plays at once

    def _returning(self, unknown: List[Detection]) -> List[str]:
        """Notes for remembered visitors not yet mentioned in this incident."""
        notes = []
        for d in unknown:
            if d.visitor_id is not None and d.seen_before and d.visitor_id not in self._seen_before:
                self._seen_before[d.visitor_id] = d.seen_before
                notes.append(d.seen_before)
        return notes

    def _unusual_time(self, now: float, mode: str) -> str | None:
        """Why a new incident is unusual ("Garden is usually quiet on Tuesdays around 3 am"), or None
        while the routine is still being learned or people are usually there at this time."""
        if mode == "off":
            return None
        try:
            judgement = self.routine(now)
        except Exception as exc:  # the incident goes on without it
            print(f"[brain] Routine check failed: {exc}")
            return None
        if judgement is None or not judgement.unusual:
            return None
        self.unusual = True
        return judgement.quiet_note(self.camera_name())

    def _alert_unusual(self, now: float, quiet: str) -> None:
        """Alerts the owner before any escalation, at most every UNUSUAL_ALERT_SECONDS per camera."""
        if self._unusual_alert_time is not None and now - self._unusual_alert_time < UNUSUAL_ALERT_SECONDS:
            return
        self._unusual_alert_time = now
        camera = self.camera_name()
        msg = f"Unrecognised person at {camera} at an unusual time: {quiet}."
        if self._seen_before:
            msg += f" {'; '.join(self._seen_before.values())}."
        title = f"{'[TEST] ' if self.simulated else ''}Unusual activity at {camera}"
        if self.notifier.send_alert(title, msg, "MEDIUM", self.snapshot()):
            self._alerted = True
            self._log("ALERT", f"Owner alerted: {msg}", "MEDIUM")
        else:
            print(f"[brain] {camera}: unusual activity alert not sent, no alert channel is set up")

    def tick(self, now: float | None = None) -> None:
        with self._lock:
            self._update(self.clock() if now is None else now)

    def _maybe_greet(self, name: str, now: float) -> None:
        cfg = self.settings.get()
        ai = cfg.ai
        # Disarmed means nothing is spoken, greetings included.
        if not ai.greet_insiders or not cfg.armed or self.incident_start is not None or self.speaker.paused():
            return
        last = self._greeted.get(name)
        if last is not None and now - last < ai.greet_cooldown_minutes * 60:
            return
        self._greeted[name] = now
        text = greeting(name)
        if ai.voice_enabled:
            self.speaker.say(text, ai.voice_rate)
        self._set_message(text, "greeting")
        self._log("GREETING", f"Greeted {name}: {text}")

    # ---- state machine --------------------------------------------------
    def _begin_incident(self, now: float, simulated: bool = False) -> None:
        self._incident_id += 1
        self.incident_start = now
        self.last_seen = now
        self.threat_level = 1
        self._peak = 1
        self._spoken_level = 0
        self._siren_fired = False
        self._alerted = False
        self.simulated = simulated
        self.said = []
        self.last_alert_time = 0.0
        self.picture_event = None
        self._seen_before = {}
        self.unusual = False

    def _update(self, now: float) -> None:
        if self.incident_start is None:
            return
        esc = self.settings.get().escalation
        if not self.manual and (self.last_seen is None or now - self.last_seen >= esc.clear_after):
            self._end_incident(now, "Person left the area")
            return
        if not self.manual:
            # Only time the person was actually on camera counts; a level must not climb
            # while the scene is empty and the incident is waiting to clear.
            elapsed = self.last_seen - self.incident_start
            level = 1 + (elapsed >= esc.level2_after) + (elapsed >= esc.level3_after) + (elapsed >= esc.level4_after)
            if level > self.threat_level:
                self.threat_level = level
                self._log_level("ESCALATION", f"Threat level {level} ({LEVEL_NAMES[level]}) after {int(elapsed)}s",
                                SEVERITY[level])
        self._peak = max(self._peak, self.threat_level)
        self._countermeasures(now)

    def _record(self) -> str | None:
        """Starts or continues the clip once the level calls for it. Returns its name when it just started."""
        if self.threat_level < self.settings.get().escalation.record_at_level and not self.manual:
            return None
        reason = "panic" if self.manual else ("test" if self.simulated else "intruder")
        fresh = not self.recorder.active
        name = self.recorder.start(reason, self.threat_level)
        return name if fresh else None

    def _log_level(self, event_type: str, description: str, severity: str) -> None:
        """Logs reaching a level. A clip due at this level starts first, so the event's picture links to it."""
        started = self._record()
        self._log(event_type, description, severity)
        if started:
            self._log("RECORDING", "Recording started", recording=started)

    def _countermeasures(self, now: float) -> None:
        cfg = self.settings.get()
        esc, level = cfg.escalation, self.threat_level
        seconds = int(now - (self.incident_start or now))
        camera = self.camera_name()

        started = self._record()
        if started:
            self._log("RECORDING", "Recording started", recording=started)
        self.recorder.note_level(level)

        # Panic alerts are sent once for the whole system by the CameraManager.
        if not self.manual and level >= esc.alert_at_level and now - self.last_alert_time >= ALERT_REPEAT_SECONDS:
            self.last_alert_time = now
            prefix = "[TEST] " if self.simulated else ""
            msg = f"Unrecognised person at {camera} for {seconds}s. Threat level {level} ({LEVEL_NAMES[level]})."
            if self._seen_before:
                msg += f" {'; '.join(self._seen_before.values())}."
            if self.notifier.send_alert(f"{prefix}Intruder at {camera}", msg, SEVERITY[level], self.snapshot()):
                self._alerted = True
                self._log("ALERT", f"Owner alerted: {msg}", SEVERITY[level])

        if not self._siren_fired and (self.manual or (esc.siren_enabled and level >= esc.siren_at_level)):
            self._siren_fired = True
            if self.speaker.siren(True, esc.siren_max_seconds):
                self._log("SIREN", f"Siren sounding on {self.speaker.describe()}", "CRITICAL")

        if self.manual:
            return  # the panic warning is spoken by the CameraManager on every camera
        if self.speaker.paused():
            return  # the owner is talking through this camera; warnings carry on when they stop
        someone_there = self.last_seen is not None and now - self.last_seen <= PRESENCE_SECONDS
        due = now - self.last_ai_time >= voice_cooldown(cfg.ai.persistence)
        if not someone_there or not (level > self._spoken_level or due):
            return
        ctx = self.context(level)
        incident = self._incident_id
        if level > self._spoken_level:
            cached = self.ai.take_cached(ctx)
            if cached:
                self.last_ai_time, self._spoken_level = now, level
                self._deliver({"text": cached, "source": "cached", "model": self.ai.last_model, "latency_ms": 0},
                              incident)
                return
        if self.ai.request_warning(lambda result: self._deliver(result, incident), ctx, self.camera_id):
            self.last_ai_time, self._spoken_level = now, level

    def context(self, level: int, manual: bool = False) -> WarningContext:
        esc = self.settings.get().escalation
        return WarningContext(
            level=level, people=max(1, self.persons), location=self.camera_name(),
            recording=self.recorder.active, alerted=self._alerted, siren=self.speaker.siren_active,
            siren_next=esc.siren_enabled and esc.siren_at_level == level + 1, manual=manual, said=list(self.said))

    def _set_message(self, text: str, source: str) -> None:
        self.last_message, self.last_message_time, self.last_message_source = text, time.time(), source

    def _deliver(self, result: dict, incident: int) -> None:
        with self._lock:
            if incident != self._incident_id or self.incident_start is None:
                return  # the person left while the model was thinking
            text = result["text"]
            self.said.append(text)
            self._set_message(text, result["source"])
        ai = self.settings.get().ai
        if ai.voice_enabled:
            self.speaker.say(text, ai.voice_rate)
        via = {"llm": f"via {result.get('model')}", "cached": f"prepared by {result.get('model') or 'the model'}",
               "fallback": f"pre-written line: {result.get('error') or 'model unavailable'}"
               }.get(result["source"], result["source"])
        latency = f", {result['latency_ms']} ms" if result.get("latency_ms") else ""
        self._log("VOICE", f"{text} ({via}{latency})")

    def _end_incident(self, now: float, reason: str) -> None:
        duration = int(now - (self.incident_start or now))
        self._log("CLEARED", f"{reason}. Incident lasted {duration}s, peak level {self._peak}.", "LOW")
        self._incident_id += 1
        self.incident_start = None
        self.last_seen = None
        self.threat_level = 0
        self.manual = False
        self.simulated = False
        self.said = []
        self.picture_event = None
        self._seen_before = {}
        self.unusual = False
        if self._siren_fired:
            self.speaker.siren(False)
        self._siren_fired = False
        self.recorder.stop()

    # ---- actions from the CameraManager -----------------------------------
    def enter_panic(self) -> None:
        now = self.clock()
        with self._lock:
            if self.incident_start is None:
                self._begin_incident(now)
            self.manual = True
            self.threat_level = 4
            self._peak = 4
            self._siren_fired = False
            self._countermeasures(now)

    def deliver_panic_message(self, text: str, source: str) -> None:
        with self._lock:
            if not self.manual:
                return
            self.said.append(text)
            self._set_message(text, source)
        ai = self.settings.get().ai
        if ai.voice_enabled:
            self.speaker.say(text, ai.voice_rate)

    def reset(self, reason: str = "Alarm reset") -> bool:
        with self._lock:
            if self.incident_start is None:
                self.speaker.siren(False)
                return False
            self._end_incident(self.clock(), reason)
            return True

    def disarm(self) -> None:
        with self._lock:
            if self.incident_start is not None and not self.manual:
                self._end_incident(self.clock(), "System disarmed")

    def speak(self, text: str) -> bool:
        ai = self.settings.get().ai
        with self._lock:
            self._set_message(text, "operator")
        self._log("VOICE", f"{text} (typed by operator)")
        return self.speaker.say(text, ai.voice_rate, interrupt=True)

    def _on_recording_finished(self, info: dict) -> None:
        label = {"panic": "panic", "test": "test", "intruder": "intrusion", "sound": "loud sound"}.get(info["reason"],
                                                                                                     info["reason"])
        peak = f" (peak level {info['max_level']})" if info["max_level"] else ""
        self._log("CLIP_SAVED", f"Saved {info['duration']}s {label} clip{peak}", recording=info["file"])
        esc = self.settings.get().escalation
        if info["max_level"] >= esc.alert_at_level or info["reason"] == "panic":
            camera = self.camera_name()
            self.notifier.send_clip(f"{'[TEST] ' if info['reason'] == 'test' else ''}Incident clip from {camera}",
                                    f"Recording of the {label} at {camera} ({info['duration']}s, "
                                    f"peak level {info['max_level']}).", info["path"])
        self.prune(self.settings.get().recording.retention_days)

    # ---- reporting -------------------------------------------------------
    def status(self) -> dict:
        now = self.clock()
        with self._lock:
            return {
                "threat_level": self.threat_level,
                "threat_label": LEVEL_NAMES[self.threat_level],
                "manual_alarm": self.manual,
                "test": self.simulated,
                "incident_started": self.incident_start,
                "incident_seconds": int(now - self.incident_start) if self.incident_start else 0,
                "persons": self.persons,
                "pending": self.pending,
                "insiders_in_view": self.insiders_in_view,
                "siren_active": self.speaker.siren_active,
                "last_message": self.last_message,
                "last_message_time": self.last_message_time,
                "last_message_source": self.last_message_source,
            }
