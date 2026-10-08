"""
Cameras.

Each enabled camera runs as a CameraUnit with its own background loop:

  frame -> motion check -> person detection -> face evidence -> tracker
        -> CameraBrain (escalation) -> annotated frame (live view + recorder)

The CameraManager creates and removes units as settings change, and handles
what applies to the whole system: arming, the panic button, resetting, the
operator speaking through a camera, and preparing voice lines while idle.
"""
import os
import threading
import time
from datetime import datetime

import cv2

from models.domain import Detection
from services.ai_service import WarningContext, ai_service
from services.audio_service import CameraSpeaker
from services.brain_service import CameraBrain
from services.detection_service import detection_service
from services.event_service import event_service
from services.face_service import face_service
from services.notification_service import notification_service
from services.phone_service import phone_hub
from services.recording_service import Recorder, recording_library
from services.schedule_service import armed_at, next_change
from services.settings_service import CameraConfig, Settings, settings_service
from services.siren_service import siren_service
from services.sources import CaptureSource, parse_source
from services.tracker import FaceEvidence, Tracker

# Avoid oversubscribing the CPU: OpenCV, PyTorch and the language model all default to one
# thread per core, and fighting over cores makes every one of them slower.
cv2.setNumThreads(max(1, (os.cpu_count() or 4) // 2))

STREAM_MAX_WIDTH = 960
HEARTBEAT_SECONDS = 2.0  # run detection at least this often even without motion
BOX_HOLD_SECONDS = 1.5

RED, GREEN, AMBER, GREY, BLUE = (40, 40, 220), (90, 180, 60), (0, 170, 240), (150, 150, 150), (230, 160, 60)


def draw_overlay(frame, detections: list[Detection], camera_name: str, armed: bool):
    # Text and lines scale with the frame so labels stay readable on HD cameras.
    scale = max(0.5, frame.shape[1] / 1100)
    thick = max(1, round(scale * 1.5))
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in d.bbox)
        if d.simulated:
            color, label = AMBER, "TEST"
        elif d.status == "known":
            color, label = GREEN, d.identity or "Insider"
        elif d.status == "pending":
            color, label = BLUE, "Checking"
        else:
            color, label = (RED if armed else GREY), "Unknown"
        label = f"{label} {d.confidence:.0%}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thick + 1)
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale, thick)
        pad = int(4 * scale)
        ty = max(y1, th + base + 2 * pad)
        cv2.rectangle(frame, (x1, ty - th - base - 2 * pad), (x1 + tw + 2 * pad, ty), color, -1)
        cv2.putText(frame, label, (x1 + pad, ty - base - pad), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * scale,
                    (255, 255, 255), thick, cv2.LINE_AA)

    stamp = f"{camera_name}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    h = frame.shape[0]
    (tw, th), base = cv2.getTextSize(stamp, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, thick)
    pad = int(5 * scale)
    cv2.rectangle(frame, (pad, h - th - base - 3 * pad), (tw + 3 * pad, h - pad), (0, 0, 0), -1)
    cv2.putText(frame, stamp, (2 * pad, h - base - 2 * pad), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale,
                (235, 235, 235), thick, cv2.LINE_AA)
    return frame


class CameraUnit:
    def __init__(self, cfg: CameraConfig, settings=settings_service, detector=detection_service, faces=face_service,
                 notifier=notification_service, events=event_service, hub=phone_hub):
        self.id = cfg.id
        self.settings, self.detector, self.faces = settings, detector, faces
        self.notifier, self.events, self.hub = notifier, events, hub
        self._cfg = cfg
        self.source = self._make_source(cfg)
        self.tracker = Tracker()
        self.recorder = Recorder(cfg.id, self.name)
        phone_send = (lambda cmd: hub.send(self.id, cmd)) if cfg.is_phone else None
        self.speaker = CameraSpeaker(cfg.id, lambda: self.cfg.audio, phone_send)
        self.brain = CameraBrain(cfg.id, self.name, self.speaker, self.recorder, settings=settings,
                                 notifier=notifier, events=events, prune=lambda days: prune_media(days, events))
        self.brain.snapshot = self.snapshot

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
        self.source.start()
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"camera:{self.id}")
        self._thread.start()
        self.recorder.start_sampler(self.latest_frame)

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=5)
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
            judging[2] = self._encode(draw_overlay(frame.copy(), detections, self.name(), self.settings.get().armed))
        return judging[2]

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
                last_tick = now
            if now - last_health >= 1.0:
                self._check_health(now)
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
                annotated = draw_overlay(frame.copy(), shown, self.name(), cfg.armed)
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
        recognition = det.face_recognition and self.faces.active
        evidence = self.faces.analyze(frame, detections, det.face_match_threshold) if recognition \
            else [FaceEvidence() for _ in detections]
        if simulating:
            h, w = frame.shape[:2]
            detections.append(Detection(class_name="person", confidence=0.99, simulated=True,
                                        bbox=[w * 0.35, h * 0.2, w * 0.65, h * 0.95]))
            evidence.append(FaceEvidence())
        self.tracker.update(detections, evidence, now, det.face_match_threshold, det.identify_seconds, recognition)
        self._detections, self._detections_time = detections, now
        self._judging = [frame, detections, None]
        try:
            self.brain.process(detections, now)
        finally:
            self._judging = None
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

    def status(self) -> dict:
        cfg = self.cfg
        src = self.source.status()
        kind, _ = parse_source(cfg.source)
        return {
            "id": cfg.id,
            "name": cfg.name,
            "source": cfg.source,
            "kind": kind,
            "audio": cfg.audio,
            **src,
            "pipeline": {"fps": self.fps if src["connected"] else 0.0, "motion": self.motion,
                         "test_seconds_left": max(0, int(self.simulate_until - time.time())), "error": self.error},
            **self.brain.status(),
            "recording": self.recorder.status(),
        }


def prune_media(retention_days: int, events=event_service) -> int:
    """Recordings and event pictures share one retention period."""
    return recording_library.prune(retention_days) + events.prune_snapshots(retention_days)


class CameraManager:
    def __init__(self, settings=settings_service, ai=ai_service, notifier=notification_service,
                 events=event_service, hub=phone_hub):
        self.settings, self.ai, self.notifier, self.events, self.hub = settings, ai, notifier, events, hub
        self.units: dict[str, CameraUnit] = {}
        self._lock = threading.RLock()
        self.panic_active = False
        self._idle_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._schedule_lock = threading.Lock()
        self._schedule_seen: tuple[str, bool] | None = None  # (schedule, its answer) last acted on

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
            self.events.log("ALERT", f"Owner alerted: {msg}", "CRITICAL", snapshot=picture)
        if not units:
            return
        ctx = units[0].brain.context(4, manual=True)
        ctx.alerted = self.notifier.status()["discord"] or self.notifier.status()["email"]
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
        notify = self.notifier.status()
        can_alert = notify["discord"] or notify["email"]
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
        """Arm or disarm when the schedule's answer changes, or when the schedule was edited.
        Arming or disarming by hand in between therefore lasts until the next change."""
        with self._schedule_lock:
            schedule = self.settings.get().schedule
            if not schedule.enabled or not schedule.rules:
                self._schedule_seen = None
                return
            seen = (schedule.model_dump_json(), armed_at(schedule.rules, now or datetime.now()))
            if seen == self._schedule_seen:
                return
            self._schedule_seen = seen
            if seen[1]:
                self.set_armed(True, by="by schedule")
            elif not self.panic_active:  # a raised alarm stays until it is reset
                self.set_armed(False, by="by schedule")

    def schedule_status(self, now: datetime | None = None) -> dict:
        schedule = self.settings.get().schedule
        if not schedule.enabled or not schedule.rules:
            return {"enabled": schedule.enabled, "active": None, "next_change": None}
        now = now or datetime.now()
        change = next_change(schedule.rules, now)
        return {"enabled": True, "active": armed_at(schedule.rules, now),
                "next_change": change.astimezone().isoformat() if change else None}

    def _idle_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_schedule()
                if self.settings.get().armed:
                    self.ai.keep_warm()
                busy = self.panic_active or any(u.brain.incident_active for u in list(self.units.values()))
                if not busy:
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
