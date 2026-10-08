"""
Threat escalation.

  0 Clear       nobody unrecognised in view
  1 Detected    unrecognised person appears          -> voice greeting
  2 Loitering   still there after level2_after secs  -> voice warning (+ recording by default)
  3 Intruder    still there after level3_after secs  -> owner alerted
  4 Alarm       still there after level4_after secs  -> siren

An incident ends when nobody unrecognised has been seen for clear_after
seconds, when the system is disarmed, or when the alarm is reset from the
dashboard. A manual panic stays at level 4 until it is reset.
"""
import threading
import time
from typing import Callable, List

from models.domain import Detection
from services.ai_service import ai_service
from services.event_service import event_service
from services.notification_service import notification_service
from services.recording_service import recording_service
from services.settings_service import settings_service
from services.siren_service import siren_service
from services.tts_service import tts_service

LEVEL_NAMES = {0: "Clear", 1: "Person detected", 2: "Loitering", 3: "Intruder", 4: "Alarm"}
SEVERITY = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}
ALERT_REPEAT_SECONDS = 120
INSIDER_LOG_SECONDS = 300
PRESENCE_SECONDS = 3  # no voice warnings once nobody has been seen for this long


def voice_cooldown(persistence: int) -> float:
    """Seconds between spoken warnings: 40 s at persistence 0, 8 s at 100."""
    return 40.0 - 0.32 * persistence


class BrainService:
    def __init__(self, settings=settings_service, ai=ai_service, tts=tts_service, siren=siren_service,
                 recorder=recording_service, notifier=notification_service, events=event_service,
                 clock: Callable[[], float] = time.time):
        self.settings, self.ai, self.tts, self.siren = settings, ai, tts, siren
        self.recorder, self.notifier, self.events, self.clock = recorder, notifier, events, clock
        self.snapshot: Callable[[], bytes | None] = lambda: None
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
        self.last_ai_time = 0.0
        self.last_alert_time = 0.0
        self.said: List[str] = []

        self.persons = 0
        self.insiders_in_view: List[str] = []
        self._insider_seen: dict[str, float] = {}
        self._insider_logged: dict[str, float] = {}

        self.last_message: str | None = None
        self.last_message_time: float | None = None
        self.last_message_source: str | None = None

        recorder.on_finished = self._on_recording_finished

    @property
    def armed(self) -> bool:
        return self.settings.get().armed

    # ---- inputs ---------------------------------------------------------
    def process(self, detections: List[Detection], now: float | None = None) -> None:
        now = self.clock() if now is None else now
        cfg = self.settings.get()
        with self._lock:
            persons = [d for d in detections if d.class_name == "person"]
            known = [d for d in persons if d.known]
            unknown = [d for d in persons if not d.known]
            self.persons = len(persons)
            self.insiders_in_view = sorted({d.identity for d in known if d.identity})

            for name in self.insiders_in_view:
                self._insider_seen[name] = now
                if now - self._insider_logged.get(name, 0) >= INSIDER_LOG_SECONDS:
                    self._insider_logged[name] = now
                    self.events.log("INSIDER", f"Recognised {name}", "INFO")

            if unknown and not any(d.simulated for d in unknown):
                # A recognised insider who turns away from the camera should not trigger an alarm.
                grace = cfg.detection.insider_grace_seconds
                recent = [n for n, t in self._insider_seen.items() if now - t <= grace]
                if recent and not any(d.face_visible for d in unknown) and len(persons) <= len(recent):
                    unknown = []

            if unknown and cfg.armed:
                if self.incident_start is None:
                    self._begin_incident(now, simulated=any(d.simulated for d in unknown))
                    self.events.log("DETECTION", "Unrecognised person detected" + (" (test)" if self.simulated else ""), "LOW")
                self.last_seen = now
            self._update(now)

    def tick(self, now: float | None = None) -> None:
        with self._lock:
            self._update(self.clock() if now is None else now)

    # ---- state machine --------------------------------------------------
    def _begin_incident(self, now: float, simulated: bool = False) -> None:
        self._incident_id += 1
        self.incident_start = now
        self.last_seen = now
        self.threat_level = 1
        self._peak = 1
        self._spoken_level = 0
        self._siren_fired = False
        self.simulated = simulated
        self.said = []
        self.last_alert_time = 0.0

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
                self.events.log("ESCALATION", f"Threat level {level} ({LEVEL_NAMES[level]}) after {int(elapsed)}s",
                                SEVERITY[level])
        self._peak = max(self._peak, self.threat_level)
        self._countermeasures(now)

    def _countermeasures(self, now: float) -> None:
        cfg = self.settings.get()
        esc, level = cfg.escalation, self.threat_level
        seconds = int(now - (self.incident_start or now))

        if level >= esc.record_at_level or self.manual:
            reason = "panic" if self.manual else ("test" if self.simulated else "intruder")
            fresh = not self.recorder.active
            name = self.recorder.start(reason, level)
            if name and fresh:
                self.events.log("RECORDING", "Recording started", "INFO", recording=name)
        self.recorder.note_level(level)

        if (level >= esc.alert_at_level or self.manual) and now - self.last_alert_time >= ALERT_REPEAT_SECONDS:
            self.last_alert_time = now
            prefix = "[TEST] " if self.simulated else ""
            title = f"{prefix}{'Alarm raised' if self.manual else 'Intruder on camera'}"
            msg = (f"{'Panic button pressed.' if self.manual else f'Unrecognised person on camera for {seconds}s.'} "
                   f"Threat level {level} ({LEVEL_NAMES[level]}).")
            if self.notifier.send_alert(title, msg, SEVERITY[level], self.snapshot()):
                self.events.log("ALERT", f"Owner alerted: {msg}", SEVERITY[level])

        if not self._siren_fired and (self.manual or (esc.siren_enabled and level >= esc.siren_at_level)):
            self._siren_fired = True
            if self.siren.start(esc.siren_max_seconds):
                self.events.log("SIREN", "Siren sounding", "CRITICAL")

        someone_there = self.manual or (self.last_seen is not None and now - self.last_seen <= PRESENCE_SECONDS)
        due = now - self.last_ai_time >= voice_cooldown(cfg.ai.persistence)
        if someone_there and (level > self._spoken_level or due):
            incident = self._incident_id
            started = self.ai.request_warning(
                lambda result: self._on_warning(result, incident),
                level=level, seconds=seconds, manual=self.manual, said=list(self.said))
            if started:
                self.last_ai_time = now
                self._spoken_level = level

    def _on_warning(self, result: dict, incident: int) -> None:
        with self._lock:
            if incident != self._incident_id or self.incident_start is None:
                return  # the person left while the model was thinking
            text = result["text"]
            self.said.append(text)
            self.last_message, self.last_message_time = text, time.time()
            self.last_message_source = result["source"]
        ai = self.settings.get().ai
        if ai.voice_enabled:
            self.tts.say(text, ai.voice_rate)
        via = f"via {result['model']}" if result["source"] == "llm" else "pre-written line, model unavailable"
        self.events.log("VOICE", f"{text} ({via}, {result['latency_ms']} ms)", "INFO")

    def _end_incident(self, now: float, reason: str) -> None:
        duration = int(now - (self.incident_start or now))
        self.events.log("CLEARED", f"{reason}. Incident lasted {duration}s, peak level {self._peak}.", "LOW")
        self._incident_id += 1
        self.incident_start = None
        self.last_seen = None
        self.threat_level = 0
        self.manual = False
        self.simulated = False
        self.said = []
        self.siren.stop()
        self.recorder.stop()

    # ---- dashboard actions ----------------------------------------------
    def trigger_panic(self) -> None:
        now = self.clock()
        with self._lock:
            if self.incident_start is None:
                self._begin_incident(now)
            self.manual = True
            self.threat_level = 4
            self._peak = 4
            self._siren_fired = False
            self.last_alert_time = 0.0
            self._spoken_level = 0
            self.events.log("PANIC", "Alarm raised from the dashboard", "CRITICAL")
            self._countermeasures(now)

    def reset_alarm(self) -> bool:
        with self._lock:
            if self.incident_start is None:
                self.siren.stop()
                return False
            self.events.log("RESET", "Alarm acknowledged from the dashboard", "INFO")
            self._end_incident(self.clock(), "Alarm reset")
            return True

    def set_armed(self, armed: bool) -> None:
        if armed == self.armed:
            return
        self.settings.update({"armed": armed})
        with self._lock:
            self.events.log("ARMED" if armed else "DISARMED",
                            "System armed" if armed else "System disarmed: detections will not raise alarms", "INFO")
            if not armed and self.incident_start is not None and not self.manual:
                self._end_incident(self.clock(), "System disarmed")

    def speak(self, text: str) -> bool:
        ai = self.settings.get().ai
        text = " ".join(text.split())[:300]
        if not text:
            return False
        with self._lock:
            self.last_message, self.last_message_time, self.last_message_source = text, time.time(), "operator"
        self.events.log("VOICE", f"{text} (typed by operator)", "INFO")
        return self.tts.say(text, ai.voice_rate, interrupt=True)

    def _on_recording_finished(self, info: dict) -> None:
        label = {"panic": "panic", "test": "test", "intruder": "intrusion"}.get(info["reason"], info["reason"])
        self.events.log("CLIP_SAVED", f"Saved {info['duration']}s {label} clip (peak level {info['max_level']})",
                        "INFO", recording=info["file"])
        esc = self.settings.get().escalation
        if info["max_level"] >= esc.alert_at_level or info["reason"] == "panic":
            self.notifier.send_clip(f"{'[TEST] ' if info['reason'] == 'test' else ''}Incident clip",
                                    f"Recording of the {label} ({info['duration']}s, peak level {info['max_level']}).",
                                    info["path"])
        self.recorder.prune(self.settings.get().recording.retention_days)

    # ---- reporting -------------------------------------------------------
    def status(self) -> dict:
        now = self.clock()
        with self._lock:
            return {
                "armed": self.armed,
                "threat_level": self.threat_level,
                "threat_label": LEVEL_NAMES[self.threat_level],
                "manual_alarm": self.manual,
                "test": self.simulated,
                "incident_started": self.incident_start,
                "incident_seconds": int(now - self.incident_start) if self.incident_start else 0,
                "persons": self.persons,
                "insiders_in_view": self.insiders_in_view,
                "last_message": self.last_message,
                "last_message_time": self.last_message_time,
                "last_message_source": self.last_message_source,
            }


brain_service = BrainService()
