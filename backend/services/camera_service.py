"""
Cameras.

Each enabled camera runs as a CameraUnit with its own background loop:

  frame -> motion check -> person detection -> face evidence -> tracker
        -> remembered visitors -> CameraBrain (escalation) -> annotated frame (live view + recorder)

A phone camera's microphone levels go to its SoundMonitor (loud-sound alerts).

The CameraManager creates and removes units as settings change, and handles
what applies to the whole system: arming, the panic button, resetting, the
operator speaking through a camera, and preparing voice lines while idle.
"""
import json
import os
import threading
import time
from datetime import datetime
from typing import Callable

import cv2
import numpy as np

from config import SCHEDULE_STATE_FILE
from models.domain import Detection
from services.ai_service import WarningContext, ai_service
from services.audio_service import CameraSpeaker
from services.brain_service import CameraBrain
from services.briefing_service import briefing_service
from services.detection_service import detection_service
from services.event_service import event_service
from services.face_service import face_service
from services.heatmap_service import heatmap_service
from services.notification_service import notification_service
from services.phone_service import phone_hub
from services.recording_service import Recorder, recording_library
from services.schedule_service import AppliedEvent, last_event, next_change
from services.settings_service import CameraConfig, Settings, settings_service
from services.siren_service import siren_service
from services.sound_service import SoundMonitor
from services.sources import CaptureSource, parse_source
from services.talk_service import audio_hub
from services.tracker import FaceEvidence, Tracker
from services.visitor_service import visitor_service
from services.zones import keep_in_zones, shape_changed

# Avoid oversubscribing the CPU: OpenCV, PyTorch and the language model all default to one
# thread per core, and fighting over cores makes every one of them slower.
cv2.setNumThreads(max(1, (os.cpu_count() or 4) // 2))

STREAM_MAX_WIDTH = 960
HEARTBEAT_SECONDS = 2.0  # run detection at least this often even without motion
BOX_HOLD_SECONDS = 1.5
PRUNE_SECONDS = 3600  # old recordings and event pictures are deleted at least this often

RED, GREEN, AMBER, GREY, BLUE = (40, 40, 220), (90, 180, 60), (0, 170, 240), (150, 150, 150), (230, 160, 60)
ZONE = (230, 230, 160)  # detection zone outlines, light cyan


def draw_overlay(frame, detections: list[Detection], camera_name: str, armed: bool, zones=()):
    # Text and lines scale with the frame so labels stay readable on HD cameras.
    scale = max(0.5, frame.shape[1] / 1100)
    thick = max(1, round(scale * 1.5))
    h, w = frame.shape[:2]
    for zone in zones:
        points = np.array([[int(x * w), int(y * h)] for x, y in zone], dtype=np.int32)
        cv2.polylines(frame, [points], True, ZONE, thick, cv2.LINE_AA)
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in d.bbox)
        if d.simulated:
            color, label = AMBER, "TEST"
        elif d.status == "known":
            color, label = GREEN, d.identity or "Insider"
        elif d.status == "pending":
            color, label = BLUE, "Checking"
        else:
            color, label = (RED if armed else GREY), d.visitor_label or "Unknown"  # a visitor's given name
        label = f"{label} {d.confidence:.0%}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thick + 1)
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale, thick)
        pad = int(4 * scale)
        ty = max(y1, th + base + 2 * pad)
        cv2.rectangle(frame, (x1, ty - th - base - 2 * pad), (x1 + tw + 2 * pad, ty), color, -1)
        cv2.putText(frame, label, (x1 + pad, ty - base - pad), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale,
                    (255, 255, 255), thick, cv2.LINE_AA)

    stamp = f"{camera_name}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    (tw, th), base = cv2.getTextSize(stamp, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, thick)
    pad = int(5 * scale)
    cv2.rectangle(frame, (pad, h - th - base - 3 * pad), (tw + 3 * pad, h - pad), (0, 0, 0), -1)
    cv2.putText(frame, stamp, (2 * pad, h - base - 2 * pad), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale,
                (235, 235, 235), thick, cv2.LINE_AA)
    return frame


class CameraUnit:
    def __init__(self, cfg: CameraConfig, settings=settings_service, detector=detection_service, faces=face_service,
                 notifier=notification_service, events=event_service, hub=phone_hub, audio=audio_hub,
                 visitors=visitor_service):
        self.id = cfg.id
        self.settings, self.detector, self.faces, self.visitors = settings, detector, faces, visitors
        self.notifier, self.events, self.hub = notifier, events, hub
        self._cfg = cfg
        self.source = self._make_source(cfg)
        self.tracker = Tracker()
        self.recorder = Recorder(cfg.id, self.name)
        self.audio = audio.relay(cfg.id)
        phone_send = (lambda cmd: hub.send(self.id, cmd)) if cfg.is_phone else None
        self.speaker = CameraSpeaker(cfg.id, lambda: self.cfg.audio, phone_send, paused=lambda: self.audio.talking)
        self.brain = CameraBrain(cfg.id, self.name, self.speaker, self.recorder, settings=settings,
                                 notifier=notifier, events=events, prune=lambda days: prune_media(days, events))
        self.brain.snapshot = self.snapshot
        self.sound = SoundMonitor(self.name, self.recorder, settings=settings, notifier=notifier, events=events)
        self.sound.snapshot = self.snapshot
        self.sound.own_sound = lambda: self.audio.talked_recently() or self.speaker.sounding
        self.sound.incident_active = lambda: self.brain.incident_active
        self.heatmap = heatmap_service

        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._annotated = None
        self._raw = None
        self._jpeg: bytes | None = None
        self._jpeg_id = 0
        self._judging: list | None = None  # [frame, detections, jpeg] while the brain judges that frame
        self._detections: list[Detection] = []
        self._detections_time = 0.0
        self.simulate_until = 0.0
        self.fps = 0.0
        self.motion = False
        self.error: str | None = None
        self._ever_online = False
        self._offline_since: float | None = None
        self._offline_alerted = False
        self._zones_warned = False

    # ---- configuration --------------------------------------------------------
    @property
    def cfg(self) -> CameraConfig:
        current = self.settings.get().camera(self.id)
        if current is not None:
            self._cfg = current
        return self._cfg

    def name(self) -> str:
        return self.cfg.name

    def _make_source(self, cfg: CameraConfig):
        if cfg.is_phone:
            return self.hub.register(cfg.id, cfg.token)
        return CaptureSource(cfg.source)

    def restart_source(self, cfg: CameraConfig) -> None:
        self.source.stop()
        self.source = self._make_source(cfg)
        self.source.start()
        self.tracker.reset()

    # ---- lifecycle -----------------------------------------------------------------
    def start(self) -> None:
        self.heatmap.load(self.id)
        self.source.start()
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"camera:{self.id}")
        self._thread.start()
        self.recorder.start_sampler(self.latest_frame)

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)
        self.heatmap.save(self.id)
        self.brain.reset("Camera stopped")
        self.recorder.shutdown()
        self.source.stop()

    def simulate(self, seconds: float) -> None:
        self.simulate_until = time.time() + seconds

    # ---- outputs ---------------------------------------------------------------------
    def latest_frame(self):
        with self._lock:
            return self._annotated

    def latest_raw(self):
        with self._lock:
            return self._raw

    def latest_jpeg(self) -> tuple[bytes | None, int]:
        with self._lock:
            return self._jpeg, self._jpeg_id

    def snapshot(self) -> bytes | None:
        """The picture that goes with an event or alert. While a frame is being judged it is that
        frame with its boxes; the streamed picture is one frame older and has no boxes yet."""
        judging = self._judging
        if judging is None:
            return self.latest_jpeg()[0]
        if judging[2] is None:
            frame, detections = judging[0], judging[1]
            judging[2] = self._encode(draw_overlay(frame.copy(), detections, self.name(), self.settings.get().armed,
                                                   self.cfg.zones))
        return judging[2]

    def raw_snapshot(self) -> bytes | None:
        raw = self.latest_raw()
        return None if raw is None else self._encode(raw)

    @staticmethod
    def _encode(annotated) -> bytes | None:
        h, w = annotated.shape[:2]
        if w > STREAM_MAX_WIDTH:
            annotated = cv2.resize(annotated, (STREAM_MAX_WIDTH, int(h * STREAM_MAX_WIDTH / w)))
        ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes() if ok else None

    @property
    def connected(self) -> bool:
        return self.source.status()["connected"]

    def picture_aspect(self) -> float | None:
        """The latest picture's width / height, or None before the first picture."""
        w, h = self.source.width, self.source.height
        return w / h if w and h else None

    def zones_mismatch(self) -> bool:
        """The picture changed shape since the zones were drawn (e.g. a phone turned on its side), so
        they now cover different places."""
        cfg = self.cfg
        return bool(cfg.zones) and shape_changed(cfg.zones_aspect, self.picture_aspect())

    # ---- loop ------------------------------------------------------------------------
    def _run(self) -> None:
        prev_gray = None
        last_frame_id = -1
        last_detect = last_tick = last_health = 0.0
        count, window = 0, time.time()

        while self._running.is_set():
            now = time.time()
            if now - last_tick >= 0.25:
                self.brain.tick(now)
                self._check_sound(now)
                last_tick = now
            if now - last_health >= 1.0:
                self._check_health(now)
                self._check_zones()
                last_health = now

            frame, frame_id, _ = self.source.get_frame()
            if frame is None or frame_id == last_frame_id:
                time.sleep(0.01)
                continue
            last_frame_id = frame_id
            cfg = self.settings.get()

            try:
                h, w = frame.shape[:2]
                small = cv2.resize(frame, (160, max(1, int(160 * h / w))))
                gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
                if prev_gray is not None and prev_gray.shape == gray.shape:
                    diff = cv2.threshold(cv2.absdiff(prev_gray, gray), 25, 255, cv2.THRESH_BINARY)[1]
                    self.motion = cv2.countNonZero(diff) > gray.size * 0.004
                prev_gray = gray

                simulating = now < self.simulate_until
                interval = cfg.detection.interval_ms / 1000.0
                if now - last_detect >= interval and (
                        self.motion or self.brain.incident_active or simulating or bool(self._detections)
                        or now - last_detect >= HEARTBEAT_SECONDS):
                    last_detect = now
                    self._detect(frame, now, cfg, simulating)

                shown = self._detections if now - self._detections_time < BOX_HOLD_SECONDS else []
                annotated = draw_overlay(frame.copy(), shown, self.name(), cfg.armed, self.cfg.zones)
                jpeg = self._encode(annotated)
                with self._lock:
                    self._annotated, self._raw = annotated, frame
                    if jpeg:
                        self._jpeg = jpeg
                        self._jpeg_id += 1
            except Exception as exc:
                self.error = f"Processing error: {exc}"
                print(f"[camera:{self.id}] {self.error}")
                time.sleep(0.5)

            count += 1
            if now - window >= 2.0:
                self.fps = round(count / (now - window), 1)
                count, window = 0, now

    def _detect(self, frame, now: float, cfg: Settings, simulating: bool) -> None:
        det = cfg.detection
        detections = self.detector.detect_persons(frame, det.confidence, det.min_person_height)
        detections = keep_in_zones(detections, frame.shape[1], frame.shape[0], self.cfg.zones)
        # Strangers' faces are remembered only while armed, like everything else that is recorded.
        # Newcomers are then checked against remembered visitors as against insiders.
        remember = det.face_recognition and det.remember_visitors and cfg.armed and self.faces.ready
        recognition = det.face_recognition and (self.faces.active or remember)
        look = recognition and (self.faces.active or self.visitors.wants_look(self.id, detections, now))
        evidence = self.faces.analyze(frame, detections, det.face_match_threshold, keep_faces=remember) \
            if look else [FaceEvidence() for _ in detections]
        if simulating:
            h, w = frame.shape[:2]
            detections.append(Detection(class_name="person", confidence=0.99, simulated=True,
                                        bbox=[w * 0.35, h * 0.2, w * 0.65, h * 0.95]))
            evidence.append(FaceEvidence())
        self.tracker.update(detections, evidence, now, det.face_match_threshold, det.identify_seconds, recognition)
        self.heatmap.add(self.id, detections, frame.shape[1], frame.shape[0], now)
        if remember:
            self.visitors.annotate(self.id, detections, evidence, now, det.face_match_threshold)
        self._detections, self._detections_time = detections, now
        self._judging = [frame, detections, None]
        try:
            self.brain.process(detections, now)
        finally:
            self._judging = None
        if remember:
            self.visitors.record(self.id, self.name(), detections, now, self.brain.picture_event)
        self.error = self.detector.error

    def _check_health(self, now: float) -> None:
        """Camera unplugged, covered phone, lost Wi-Fi: log it and alert while armed."""
        if self.connected:
            if self._offline_since is not None:
                self.events.log("CAMERA_ONLINE", f"Video is back after {int(now - self._offline_since)}s", "INFO",
                                camera=self.name())
            self._ever_online, self._offline_since, self._offline_alerted = True, None, False
            return
        if not self._ever_online:
            return  # never connected yet: nothing has been lost
        if self._offline_since is None:
            self._offline_since = now
            self.events.log("CAMERA_OFFLINE", "Camera stopped sending video", "MEDIUM", camera=self.name())
        cfg = self.settings.get()
        limit = cfg.escalation.offline_alert_seconds
        if limit and cfg.armed and not self._offline_alerted and now - self._offline_since >= limit:
            self._offline_alerted = True
            msg = (f"{self.name()} has sent no video for {int(now - self._offline_since)}s. It may have been "
                   "unplugged, covered, or lost power or Wi-Fi.")
            if self.notifier.send_alert(f"Camera offline: {self.name()}", msg, "HIGH"):
                self.events.log("ALERT", f"Owner alerted: {msg}", "HIGH", camera=self.name())

    def _check_sound(self, now: float) -> None:
        try:
            for when, db in self.audio.take_levels():
                self.sound.feed(db, when)
            self.sound.tick(now)
        except Exception as exc:  # keep the camera running
            print(f"[camera:{self.id}] Sound check failed: {exc}")

    def _check_zones(self) -> None:
        mismatch = self.zones_mismatch()
        if mismatch and not self._zones_warned:
            self.events.log("SYSTEM", "The picture changed shape since the detection zones were drawn, so they now "
                            "cover different places. Redraw them in Settings → Cameras → Zones.", "MEDIUM",
                            camera=self.name())
        self._zones_warned = mismatch

    def status(self) -> dict:
        cfg = self.cfg
        src = self.source.status()
        kind, _ = parse_source(cfg.source)
        return {
            "id": cfg.id,
            "name": cfg.name,
            "source": cfg.source,
            "kind": kind,
            "audio": {"output": cfg.audio, **self.audio.status()},
            **src,
            "pipeline": {"fps": self.fps if src["connected"] else 0.0, "motion": self.motion,
                         "test_seconds_left": max(0, int(self.simulate_until - time.time())), "error": self.error},
            **self.brain.status(),
            "recording": self.recorder.status(),
            "zones_mismatch": self.zones_mismatch(),
        }


def prune_media(retention_days: int, events=event_service, visitors=visitor_service) -> int:
    """Recordings and event pictures share one retention period; remembered visitors have their own."""
    return recording_library.prune(retention_days) + events.prune_snapshots(retention_days) + visitors.prune()


class CameraManager:
    def __init__(self, settings=settings_service, ai=ai_service, notifier=notification_service,
                 events=event_service, hub=phone_hub, schedule_file=SCHEDULE_STATE_FILE):
        self.settings, self.ai, self.notifier, self.events, self.hub = settings, ai, notifier, events, hub
        self.units: dict[str, CameraUnit] = {}
        self._lock = threading.RLock()
        self.panic_active = False
        self._idle_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._schedule_lock = threading.Lock()
        self._schedule_applied = AppliedEvent(schedule_file)
        self._schedule_rules: str | None = None  # the periods last checked, to notice edits
        self.schedule_waiting = False  # a scheduled disarm is waiting for an alarm to end
        self.prune: Callable[[int], int] = lambda days: prune_media(days, self.events)
        self._pruned_at = time.monotonic()  # the server prunes once when it starts

    # ---- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        self.apply(None, self.settings.get())
        self._stop.clear()
        self._idle_thread = threading.Thread(target=self._idle_loop, daemon=True, name="background")
        self._idle_thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for unit in self.units.values():
                unit.stop()
            self.units.clear()

    def apply(self, old: Settings | None, new: Settings) -> None:
        """Bring running cameras in line with the settings."""
        rec = new.recording
        with self._lock:
            desired = {c.id: c for c in new.cameras if c.enabled}
            for cam_id in [i for i in self.units if i not in desired]:
                self.units.pop(cam_id).stop()
                self.hub.unregister(cam_id)
            for cam_id, cfg in desired.items():
                unit = self.units.get(cam_id)
                if unit is None:
                    unit = self.units[cam_id] = CameraUnit(cfg, self.settings, notifier=self.notifier,
                                                           events=self.events, hub=self.hub)
                    unit.recorder.configure(rec.preroll_seconds, rec.postroll_seconds, rec.max_clip_seconds)
                    unit.start()
                    continue
                before = old.camera(cam_id) if old else None
                if before is None or before.source != cfg.source or before.token != cfg.token:
                    unit.restart_source(cfg)
                unit.recorder.configure(rec.preroll_seconds, rec.postroll_seconds, rec.max_clip_seconds)
            for cam_id in [c.id for c in new.cameras if not c.enabled]:
                self.hub.unregister(cam_id)

    def get(self, camera_id: str) -> CameraUnit:
        unit = self.units.get(camera_id)
        if unit is None:
            raise KeyError(camera_id)
        return unit

    def first_online(self) -> CameraUnit | None:
        return next((u for u in self.units.values() if u.connected), None)

    # ---- system-wide actions ------------------------------------------------------
    def set_armed(self, armed: bool, by: str = "") -> None:
        if armed == self.settings.get().armed:
            return
        self.settings.update({"armed": armed})
        how = f" {by}" if by else ""
        self.events.log("ARMED" if armed else "DISARMED",
                        f"System armed{how}" if armed else f"System disarmed{how}: detections will not raise alarms", "INFO")
        if not armed:
            for unit in list(self.units.values()):
                unit.brain.disarm()

    def panic(self) -> None:
        with self._lock:
            units = list(self.units.values())
            self.panic_active = True
        first = self.first_online()
        picture = first.snapshot() if first else None
        self.events.log("PANIC", "Alarm raised from the dashboard", "CRITICAL", snapshot=picture)
        for unit in units:
            unit.brain.enter_panic()
        cameras = ", ".join(u.name() for u in units) or "no cameras"
        msg = f"Panic button pressed. Recording on {cameras}; the siren is sounding."
        if self.notifier.send_alert("Alarm raised", msg, "CRITICAL", picture):
            self.events.log("ALERT", f"Owner alerted: {msg}", "CRITICAL", snapshot=picture,
                            recording=first.recorder.file if first else None)
        if not units:
            return
        ctx = units[0].brain.context(4, manual=True)
        ctx.alerted = self.notifier.status()["any"]
        cached = self.ai.take_cached(ctx)
        if cached:
            self._broadcast({"text": cached, "source": "cached"})
        else:
            self.ai.request_warning(self._broadcast, ctx, camera_id="__panic__")

    def _broadcast(self, result: dict) -> None:
        if not self.panic_active:
            return
        for unit in list(self.units.values()):
            unit.brain.deliver_panic_message(result["text"], result["source"])
        self.events.log("VOICE", f"{result['text']} (panic warning on every camera)", "INFO")

    def reset_alarm(self) -> bool:
        with self._lock:
            was_panic, self.panic_active = self.panic_active, False
            units = list(self.units.values())
        was_active = False
        for unit in units:
            was_active = unit.brain.reset() or was_active
        siren_service.stop()
        if was_active or was_panic:
            self.events.log("RESET", "Alarm acknowledged from the dashboard", "INFO")
        self.check_schedule()  # a scheduled disarm that waited for this alarm happens now
        return was_active or was_panic

    def speak(self, text: str, camera_id: str | None = None) -> bool:
        text = " ".join(text.split())[:300]
        if not text:
            return False
        units = [self.get(camera_id)] if camera_id else list(self.units.values())
        spoken = False
        for unit in units:
            spoken = unit.brain.speak(text) or spoken
        return spoken

    # ---- background voice preparation --------------------------------------------
    def warning_contexts(self) -> list[WarningContext]:
        """The situations each level will be in, so their first line can be written in advance."""
        esc = self.settings.get().escalation
        can_alert = self.notifier.status()["any"]
        siren_ok = siren_service.available or any(u.cfg.is_phone and u.cfg.audio != "server" for u in self.units.values())
        out = [WarningContext(level=level, recording=level >= esc.record_at_level,
                              alerted=can_alert and level >= esc.alert_at_level,
                              siren=esc.siren_enabled and siren_ok and level >= esc.siren_at_level,
                              siren_next=esc.siren_enabled and esc.siren_at_level == level + 1)
               for level in (1, 2, 3, 4)]
        out.append(WarningContext(level=4, manual=True, recording=True, alerted=can_alert, siren=siren_ok))
        return out

    # ---- schedule -------------------------------------------------------------------
    def check_schedule(self, now: datetime | None = None) -> None:
        """Act on each schedule event once: arm where a period starts, disarm where the armed time ends.
        Arming or disarming by hand lasts until the next event, also across restarts. Turning the
        schedule on applies it at once; editing it acts only if that changes its answer for now."""
        with self._schedule_lock:
            schedule = self.settings.get().schedule
            if not schedule.enabled or not schedule.rules:
                self._schedule_applied.remember(None)
                self._schedule_rules, self.schedule_waiting = None, False
                return
            now = (now or datetime.now()).astimezone()
            event = last_event(schedule.rules, now)
            if event is None:
                return
            rules = json.dumps([r.model_dump() for r in schedule.rules])
            applied = self._schedule_applied.event
            if applied is None or applied[0] > now:  # just turned on, or the clock was put back
                due = True
            elif self._schedule_rules not in (None, rules):  # edited
                due = event[1] != applied[1]
            else:
                due = event[0] > applied[0]
            if due and not event[1] and self.settings.get().armed and self.alarm_raised():
                # A scheduled disarm never silences an alarm; it is tried again until the alarm ends
                if not self.schedule_waiting:
                    self.schedule_waiting = True
                    self.events.log("SYSTEM", "Scheduled disarm waits until the alarm is reset or clears", "INFO")
                return
            self._schedule_rules, self.schedule_waiting = rules, False
            self._schedule_applied.remember(event)
            if due:
                self.set_armed(event[1], by="by schedule")

    def alarm_raised(self) -> bool:
        """A panic, or an incident that has reached the level that alerts the owner."""
        alert_at = self.settings.get().escalation.alert_at_level
        return self.panic_active or any(u.brain.manual or u.brain.threat_level >= alert_at
                                        for u in list(self.units.values()))

    def schedule_status(self, now: datetime | None = None) -> dict:
        cfg = self.settings.get()
        schedule = cfg.schedule
        if not schedule.enabled or not schedule.rules:
            return {"enabled": schedule.enabled, "active": None, "next_change": None, "waiting": False}
        now = (now or datetime.now()).astimezone()
        event = last_event(schedule.rules, now)
        change = next_change(schedule.rules, now, cfg.armed)
        return {"enabled": True, "active": bool(event and event[1]),
                "next_change": change.isoformat() if change else None, "waiting": self.schedule_waiting}

    # ---- clean-up -------------------------------------------------------------------
    def prune_old_media(self, now: float | None = None) -> None:
        """Applies the retention period hourly. Saving a clip prunes too, but pictures of recognised
        people and of incidents below the recording level build up without any clip."""
        now = time.monotonic() if now is None else now
        if now - self._pruned_at < PRUNE_SECONDS:
            return
        self._pruned_at = now
        self.prune(self.settings.get().recording.retention_days)

    def _idle_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_schedule()
                self.prune_old_media()
                visitor_service.expire(time.time())  # people who left while no camera checked for visitors
                heatmap_service.tick()
                if self.settings.get().armed:
                    self.ai.keep_warm()
                busy = self.panic_active or any(u.brain.incident_active for u in list(self.units.values()))
                if not busy:
                    briefing_service.run_if_due()  # waits for a quiet moment, as it uses the model too
                    self.ai.prefetch(self.warning_contexts())
            except Exception as exc:  # keep the schedule and voice preparation running
                print(f"[manager] Background task failed: {exc}")
            self._stop.wait(10)

    # ---- reporting ----------------------------------------------------------------
    def status(self) -> dict:
        cameras = [u.status() for u in list(self.units.values())]
        level = max([c["threat_level"] for c in cameras], default=0)
        return {
            "armed": self.settings.get().armed,
            "panic": self.panic_active,
            "threat_level": level,
            "alarm_cameras": [c["name"] for c in cameras if c["threat_level"] >= 3 or c["manual_alarm"]],
            "cameras": cameras,
        }


camera_manager = CameraManager()
