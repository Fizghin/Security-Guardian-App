"""
The visual threat sentinel for one camera: bags left behind (bag_watch.py), people
who may have fallen (fall_watch.py) and watch spots such as a gate or a window
(spot_watch.py).

  UNATTENDED  "Bag left unattended on Porch for 2 min"      MEDIUM, alert while armed
  FALL        "Someone may have fallen on Hallway and ..."   HIGH, alert armed or not: it is about
                                                             someone's safety, insiders included
  SPOT        "Back gate looks different (opened or moved)"  MEDIUM, alert while armed

Each ends with an INFO event: the bag was picked up, the person is up again, the
spot is back to normal. The camera's loop hands the sentinel every detection run
and frame and it decides there, quickly; drawing and saving the event pictures
and sending alerts happen in one background thread shared by every camera.
"""
import queue
import threading
from datetime import datetime
from typing import Callable

import cv2
import numpy as np

from models.domain import Detection
from services.bag_watch import BagWatch
from services.event_service import event_service
from services.fall_watch import FallWatch
from services.notification_service import notification_service
from services.pose_service import pose_service
from services.settings_service import settings_service
from services.spot_watch import SpotWatch

ALERT_REPEAT_SECONDS = 300       # bags and each watch spot, per camera
FALL_ALERT_REPEAT_SECONDS = 60
PEOPLE_HOLD_SECONDS = 5.0        # how long a detection run's people count for the watch spots
ASK_TEXT = "Are you OK? I've let the owner know."
PICTURE_WIDTH = 960
HALF_WIDTH = 480                 # each side of a before-and-after picture

AMBER, RED, GREEN, SPOT = (0, 170, 240), (40, 40, 220), (90, 180, 60), (235, 160, 210)  # BGR; spots light violet


def duration(seconds: float) -> str:
    return f"{int(seconds)} s" if seconds < 60 else f"{round(seconds / 60)} min"


def _clock(when: float, fmt: str = "%H:%M:%S") -> str:
    return datetime.fromtimestamp(when).strftime(fmt)


def _resize(img, width: int):
    h, w = img.shape[:2]
    return img if w == width else cv2.resize(img, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)


def _label(img, text: str, x: int, y: int, colour, scale: float, thick: int) -> None:
    """A filled label whose bottom-left corner is at (x, y), kept inside the picture."""
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, thick)
    pad = int(3 * scale) + 1
    x = max(0, min(x, img.shape[1] - tw - 2 * pad))
    y = max(th + base + 2 * pad, min(y, img.shape[0]))
    cv2.rectangle(img, (x, y - th - base - 2 * pad), (x + tw + 2 * pad, y), colour, -1)
    cv2.putText(img, text, (x + pad, y - base - pad), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (255, 255, 255), thick,
                cv2.LINE_AA)


def _polygon(polygon: list[list[float]], w: int, h: int) -> np.ndarray:
    return np.array([[int(x * w), int(y * h)] for x, y in polygon], dtype=np.int32)


def _encode(img) -> bytes | None:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None


def picture(frame, caption: str, boxes=(), polygons=()) -> bytes | None:
    """The frame with boxes [(box, colour, label)] and polygons [(polygon, colour, label)] outlined."""
    img = _resize(frame, min(PICTURE_WIDTH, frame.shape[1])).copy()
    f = img.shape[1] / frame.shape[1]
    h, w = img.shape[:2]
    scale, thick = max(0.6, w / 1100), max(1, round(w / 600))
    for polygon, colour, text in polygons:
        points = _polygon(polygon, w, h)
        cv2.polylines(img, [points], True, colour, thick + 1, cv2.LINE_AA)
        _label(img, text, int(points[:, 0].min()), int(points[:, 1].min()) - 2, colour, scale, thick)
    for box, colour, text in boxes:
        x1, y1, x2, y2 = (int(v * f) for v in box)
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, thick + 2)
        _label(img, text, x1, y1 - 2, colour, scale, thick)
    _label(img, caption, 4, h - 4, (0, 0, 0), scale, thick)
    return _encode(img)


def before_after(before: tuple[float, np.ndarray] | None, frame, now: float, polygon, caption: str) -> bytes | None:
    """Before and after side by side, the spot outlined on both. Without a before picture: just after."""
    after = _resize(frame, HALF_WIDTH).copy()
    h, w = after.shape[:2]
    scale, thick = max(0.6, w / 700), 1
    sides = [(after, f"Now {_clock(now)}")]
    if before is not None:
        sides.insert(0, (cv2.resize(before[1], (w, h)), f"Before {_clock(before[0])}"))
    else:
        sides[0] = (after, f"Now {_clock(now)} (no earlier picture)")
    for img, text in sides:
        cv2.polylines(img, [_polygon(polygon, w, h)], True, AMBER, 2, cv2.LINE_AA)
        _label(img, text, 4, 4, (0, 0, 0), scale, thick)  # pushed down to fit
    joined = np.hstack([sides[0][0], np.full((h, 4, 3), 255, np.uint8), sides[1][0]]) if len(sides) == 2 \
        else sides[0][0]
    _label(joined, caption, 4, h - 4, (0, 0, 0), scale, thick)
    return _encode(joined)


class Worker:
    """Draws and saves event pictures and sends alerts for every camera, one job at a time."""

    def __init__(self, size: int = 64):
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue(size)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._dropped = False

    def submit(self, job: Callable[[], None]) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, daemon=True, name="sentinel")
                self._thread.start()
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            if not self._dropped:
                self._dropped = True
                print("[sentinel] Saving events is falling behind; some were skipped")

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            try:
                job()
            except Exception as exc:  # keep handling the next ones
                print(f"[sentinel] Could not save an event: {exc}")


worker = Worker()


class Sentinel:
    def __init__(self, camera_name: Callable[[], str], speaker, recorder, settings=settings_service,
                 notifier=notification_service, events=event_service, poses=pose_service,
                 submit: Callable[[Callable[[], None]], None] | None = None):
        self.camera_name, self.speaker, self.recorder = camera_name, speaker, recorder
        self.settings, self.notifier, self.events, self.poses = settings, notifier, events, poses
        self.submit = submit or worker.submit
        self.bags = BagWatch()
        self.falls = FallWatch()
        self.spots = SpotWatch([])
        self._people: list[list[float]] = []
        self._people_at = float("-inf")
        self._last_alert: dict[str, float] = {}
        self._accept = False

    def reset(self) -> None:
        """The camera's picture comes from somewhere else now: start watching afresh."""
        self.bags.reset()
        self.falls.reset()
        self.spots = SpotWatch(self.spots.config)

    # ---- camera thread ---------------------------------------------------------------
    def detected(self, frame, everyone: list[Detection], people: list[Detection], bags: list[Detection],
                 now: float) -> None:
        """After each detection run. everyone: all people found, wherever they are; people: those in the
        zones, tracked; bags: bags in the zones."""
        try:
            det = self.settings.get().detection
            boxes = [d.bbox for d in everyone if not d.simulated]
            self._people, self._people_at = boxes, now
            h, w = frame.shape[:2]
            if det.unattended_minutes:
                for happening in self.bags.update(bags, boxes, now, w, h, det.unattended_minutes * 60):
                    self._bag(happening, frame, now)
            elif self.bags.started is not None:
                self.bags.reset()
            if det.fall_alerts != "off":
                tracked = [(d.track_id, d.bbox) for d in people if not d.simulated and d.track_id is not None]

                def estimate(found: list[list[float]]):
                    # The pose model loads the first time someone looks possibly down.
                    return self.poses.estimate(frame, found) if self.poses.ready() else [None] * len(found)

                for happening in self.falls.update(tracked, now, estimate):
                    self._fall(happening, frame, now)
            else:
                self.falls.reset()
        except Exception as exc:  # keep the camera running
            print(f"[sentinel] {self.camera_name()}: {exc}")

    def watch(self, frame, now: float, spots) -> None:
        """For every frame: the camera's watch spots (each is looked at twice a second)."""
        try:
            config = [(s.name, s.polygon) for s in spots]
            if config != self.spots.config:
                self.spots = SpotWatch(config)
            if self._accept:
                self._accept = False
                for name in self.spots.accept():
                    self.events.log("SPOT", f"{name}: how it looks now counts as normal (marked from the dashboard)",
                                    camera=self.camera_name())
            people = self._people if now - self._people_at <= PEOPLE_HOLD_SECONDS else []
            for happening in self.spots.update(frame, people, now):
                self._spot(happening, frame, now)
        except Exception as exc:
            print(f"[sentinel] {self.camera_name()}: {exc}")

    def accept_spots(self) -> list[str]:
        """The changed spots' new look becomes their normal, from the next frame on."""
        names = [s.name for s in self.spots.spots if s.changed]
        self._accept = bool(names)
        return names

    def draw(self, frame, now: float) -> None:
        """Watch spots, bags standing still and people who may have fallen, on the live picture."""
        h, w = frame.shape[:2]
        scale = max(0.5, w / 1100)
        thick = max(1, round(scale))
        for spot in self.spots.spots:
            colour = AMBER if spot.changed else SPOT
            points = _polygon(spot.polygon, w, h)
            cv2.polylines(frame, [points], True, colour, thick, cv2.LINE_AA)
            text = f"{spot.name}: changed" if spot.changed else spot.name
            (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, thick)
            x = max(0, min(int(points[:, 0].min()) + 3, w - tw))
            y = max(th + 2, int(points[:, 1].min()) - 4)
            cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
            cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, colour, thick, cv2.LINE_AA)
        for box, still in self.bags.shown(now):
            x1, y1, x2, y2 = (int(v) for v in box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), AMBER, thick + 1)
            _label(frame, f"Bag {int(still) // 60}:{int(still) % 60:02d}", x1, y1 - 1, AMBER, scale, thick)
        for person in self.falls.fallen:
            x1, _, _, y2 = (int(v) for v in person.bbox)
            _label(frame, "May have fallen", x1, y2, RED, scale, thick)

    # ---- events ---------------------------------------------------------------------------
    def _alert_due(self, key: str, now: float, every: float) -> bool:
        if now - self._last_alert.get(key, float("-inf")) < every:
            return False
        self._last_alert[key] = now
        return True

    def _caption(self, camera: str, now: float) -> str:
        return f"{camera}  {_clock(now, '%Y-%m-%d %H:%M:%S')}"

    def _alert(self, title: str, message: str, severity: str, image: bytes | None, recording: str | None,
               camera: str) -> bool:
        """Sends an alert and logs it. Background thread."""
        if not self.notifier.send_alert(title, message, severity, image):
            return False
        self.events.log("ALERT", f"Owner alerted: {message}", severity, recording=recording, camera=camera,
                        snapshot=image)
        return True

    def _bag(self, happening, frame, now: float) -> None:
        camera, recording = self.camera_name(), self.recorder.file
        box, caption = list(happening.bag.bbox), self._caption(camera, now)
        if happening.kind == "left":
            text = f"Bag left unattended on {camera} for {duration(happening.seconds)}"
            alert = self.settings.get().armed and self._alert_due("bag", now, ALERT_REPEAT_SECONDS)

            def job():
                image = picture(frame, caption, boxes=[(box, AMBER, "Left here")])
                self.events.log("UNATTENDED", text, "MEDIUM", recording=recording, camera=camera, snapshot=image)
                if alert:
                    self._alert(f"Bag left at {camera}", f"{text}, with nobody near it.", "MEDIUM", image, recording,
                                camera)
        else:
            def job():
                image = picture(frame, caption, boxes=[(box, GREEN, "Was here")])
                self.events.log("UNATTENDED", f"The bag on {camera} was picked up", "INFO", recording=recording,
                                camera=camera, snapshot=image)
        self.submit(job)

    def _fall(self, happening, frame, now: float) -> None:
        cfg = self.settings.get()
        camera, recording = self.camera_name(), self.recorder.file
        box, caption = list(happening.bbox), self._caption(camera, now)
        if happening.kind == "fallen":
            text = f"Someone may have fallen on {camera} and hasn't got up for {int(happening.seconds)} s"
            # A safety alert: sent whether or not the system is armed, and for insiders too
            alert = cfg.detection.fall_alerts == "alert" and self._alert_due("fall", now, FALL_ALERT_REPEAT_SECONDS)
            ask = cfg.detection.fall_ask and cfg.ai.voice_enabled

            def job():
                image = picture(frame, caption, boxes=[(box, RED, "May have fallen")])
                self.events.log("FALL", text, "HIGH", recording=recording, camera=camera, snapshot=image)
                if alert and self._alert(f"Possible fall at {camera}", f"{text}.", "HIGH", image, recording, camera) \
                        and ask and self.speaker.say(ASK_TEXT, cfg.ai.voice_rate):
                    self.events.log("VOICE", f"{ASK_TEXT} (asked after a possible fall)", camera=camera)
        else:
            up = happening.kind == "up"
            text = (f"Someone who may have fallen on {camera} is up again" if up
                    else f"Someone who may have fallen on {camera} can no longer be seen")

            def job():
                image = picture(frame, caption, boxes=[(box, GREEN, "Up again")] if up else [])
                self.events.log("FALL", text, "INFO", recording=recording, camera=camera, snapshot=image)
        self.submit(job)

    def _spot(self, happening, frame, now: float) -> None:
        spot = happening.spot
        camera, recording = self.camera_name(), self.recorder.file
        polygon, caption = spot.polygon, self._caption(camera, now)
        if happening.kind == "changed":
            text = f"{spot.name} looks different (opened or moved) since {_clock(happening.since, '%H:%M')}"
            before = spot.before_change
            alert = self.settings.get().armed and self._alert_due(f"spot:{spot.name}", now, ALERT_REPEAT_SECONDS)

            def job():
                image = before_after(before, frame, now, polygon, caption)
                self.events.log("SPOT", text, "MEDIUM", recording=recording, camera=camera, snapshot=image)
                if alert:
                    self._alert(f"{spot.name} changed at {camera}", f"{text}.", "MEDIUM", image, recording, camera)
        else:
            def job():
                image = picture(frame, caption, polygons=[(polygon, GREEN, spot.name)])
                self.events.log("SPOT", f"{spot.name} is back to normal", "INFO", recording=recording, camera=camera,
                                snapshot=image)
        self.submit(job)

    # ---- reporting ----------------------------------------------------------------------------
    def status(self, spots) -> dict:
        """spots: the camera's watch spots as set now (the camera may not have looked at them yet)."""
        names = {s.name for s in spots}
        return {
            "spots": len(spots),
            "changed_spots": [{"name": s.name, "since": s.changed_since} for s in self.spots.spots
                              if s.changed and s.name in names],
            "left_bags": len(self.bags.left),
            "fallen": len(self.falls.fallen),
        }
