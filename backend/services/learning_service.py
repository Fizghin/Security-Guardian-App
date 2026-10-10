"""
Guardian learns from the owner's feedback, and gets better at recognising the household by itself.

Feedback on an event (POST /api/events/{id}/feedback):

  real          "Correct": someone really was there. Learned spots that the event's boxes overlap
                are removed.
  false_alarm   Each unrecognised person box in the event teaches an ignored spot on its camera. A box
                that overlaps a spot already there (IoU >= SPOT_IOU) strengthens it instead: the spot
                becomes the average of the boxes taught and counts one more.
  wrong_person  "Not Alice" on an event that recognised Alice: the photos of Alice learned from that
                sighting are set aside, and that tracked person is not learned from again.

Clearing a verdict undoes what it taught; changing it undoes the old lesson first. With
learning.learn_from_feedback off the verdict is still recorded but teaches nothing.

Ignored spots (judge(), on the camera thread after the tracker): an unrecognised or not yet identified
person is ignored while ALL of these hold: their box overlaps a spot, it has stayed still since their
track began (moved less than STILL_SHIFT of the picture, about the same size), and no face was ever
seen on that track. Someone who walks into the spot moves or shows a face, so they are never hidden,
and an ignored track that moves or shows a face counts again at once. Insiders are never touched, and
nothing is learned from test intrusions.

Suggestions: an unrecognised person track that stays still with no face for SUGGEST_AFTER seconds is
suggested as a spot, with a SYSTEM event and its picture. Nothing is ignored until the owner confirms
it. At most one suggestion per place; a dismissed place is not suggested again for DISMISS_FOR.

Faces (offer_faces(), camera thread): with learning.improve_faces on, a face is added to an insider's
photos when the tracker confirmed them from several agreeing looks with no votes for anyone else, this
face itself matched them LEARN_MARGIN above the threshold and passed the quality gate, and it is unlike
their photos so far (a new angle or light). At most one per insider per LEARN_EVERY and MAX_LEARNED
learned photos per insider: the oldest learned one makes way, never a photo the owner added.

The camera thread only checks things in memory. Saving photos, pictures and events happens on one
background thread; spots, suggestions and dismissed places are stored in storage/learning/spots.json.
"""
import json
import os
import queue
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from sqlalchemy import func

from config import LEARNING_DIR
from models.database import SecurityEvent, SessionLocal, utcnow
from models.domain import Detection
from services.event_service import event_service
from services.face_service import face_service
from services.settings_service import settings_service
from services.tracker import FaceEvidence, Track, iou

SPOT_IOU = 0.5
STILL_SHIFT = 0.03      # of the picture's width or height, since the track began
STILL_RESIZE = 1.2      # the box's width and height stay within this ratio of where it began
SUGGEST_AFTER = 600.0   # seconds still, with no face, before a spot is suggested
DISMISS_FOR = 86400.0   # a dismissed place is not suggested again for this long
LEARN_MARGIN = 0.08     # a face learned from must match this far above the threshold...
LEARN_AGREE = 3         # ...on a track with at least this many looks agreeing...
LEARN_NOVEL = 0.85      # ...and be less alike than this to all of the insider's photos
LEARN_EVERY = 600.0     # at most one learned photo per insider this often
MAX_LEARNED = 12        # learned photos kept per insider
SAME_SIGHTING = 300.0   # "Not Alice" takes back Alice's photos learned this close to the event...
SAME_TRACK = 3600.0     # ...and those learned from the same tracked person within this time
SAVE_EVERY = 60.0       # when spots last matched is saved at most this often
FEEDBACK_DAYS = 30
MAX_PEOPLE = 20         # people kept in an event's details
QUEUE_SIZE = 64

VERDICTS = ("real", "false_alarm", "wrong_person")
PEOPLE_EVENTS = ("DETECTION", "ESCALATION", "ALERT")
AMBER = (0, 170, 240)


class LearningError(ValueError):
    pass


def _box(bbox: list[float], width: int, height: int) -> list[float]:
    """A box in pixels as fractions of the picture."""
    x1, y1, x2, y2 = bbox
    return [round(min(1.0, max(0.0, v)), 3) for v in (x1 / width, y1 / height, x2 / width, y2 / height)]


def event_details(camera_id: str, width: int, height: int, detections: list[Detection]) -> dict | None:
    """What an event's picture showed, kept with the event so feedback has something to learn from:
    each person's box (as fractions of the picture), confidence, status, identity, track id, whether
    a face was visible and how well it matched. None when nobody is in it."""
    if not width or not height:
        return None
    people = []
    for d in [d for d in detections if d.class_name == "person"][:MAX_PEOPLE]:
        person = {"box": _box(d.bbox, width, height), "confidence": round(d.confidence, 2), "status": d.status,
                  "identity": d.identity, "track_id": d.track_id, "face_visible": d.face_visible,
                  "match_score": d.match_score}
        if d.simulated:
            person["simulated"] = True
        people.append(person)
    return {"camera_id": camera_id, "people": people} if people else None


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _photos(n: int) -> str:
    return f"{n} photo{'' if n == 1 else 's'}"


class LearningService:
    def __init__(self, directory: Path = LEARNING_DIR, settings=settings_service, faces=face_service,
                 events=event_service):
        self.dir = directory
        self.path = directory / "spots.json"
        self.settings, self.faces, self.events = settings, faces, events
        self._lock = threading.RLock()  # memory; never held while waiting for the face models
        self._write = threading.Lock()  # spots.json
        self._feedback = threading.Lock()  # one verdict change at a time
        self._spots: list[dict] = []
        self._suggestions: list[dict] = []
        self._dismissed: list[dict] = []
        self._restore: dict[str, list[dict]] = {}  # event id -> spots its "Correct" removed
        self._dirty = False
        self._saved_at = 0.0
        self._loaded = False
        self._looks: dict[tuple[str, int], int] = {}  # (camera id, track id) -> looks agreeing with its identity
        self._blocked: set[tuple[str, int]] = set()  # tracked people the owner said were someone else
        self._learned_at: dict[str, float] = {}  # insider -> when a photo of them was last learned
        self._learning: set[str] = set()  # insiders with a photo waiting to be saved
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue(QUEUE_SIZE)
        self._worker: threading.Thread | None = None

    # ---- storage -----------------------------------------------------------------------
    def load(self) -> None:
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"[learning] Could not read {self.path}: {exc}")
        data = data if isinstance(data, dict) else {}

        def records(key):
            value = data.get(key)
            return [r for r in value if isinstance(r, dict) and isinstance(r.get("box"), list) and len(r["box"]) == 4] \
                if isinstance(value, list) else []

        learned_at: dict[str, float] = {}  # so the limit of one photo per LEARN_EVERY holds across restarts
        for photo in self.faces.learned_photos():
            learned_at[photo["name"]] = max(learned_at.get(photo["name"], 0.0), photo["learned"])
        with self._lock:
            self._spots, self._suggestions, self._dismissed = records("spots"), records("suggestions"), records("dismissed")
            restore = data.get("restore")
            self._restore = restore if isinstance(restore, dict) else {}
            self._learned_at = learned_at
            self._loaded = True

    def _save(self) -> None:
        with self._write:
            with self._lock:
                now = time.time()
                self._dismissed = [d for d in self._dismissed if d.get("until", 0) > now]
                text = json.dumps({"spots": self._spots, "suggestions": self._suggestions,
                                   "dismissed": self._dismissed, "restore": self._restore}, indent=1)
                self._dirty, self._saved_at = False, time.monotonic()
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ---- background work -----------------------------------------------------------------
    def start(self) -> None:
        """Loads what was learned and starts saving in the background."""
        self.load()
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, daemon=True, name="learning")
            self._worker.start()

    def _work(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=SAVE_EVERY / 4)
            except queue.Empty:
                job = None
            self._run(job)

    def _run(self, job: Callable[[], None] | None) -> None:
        try:
            if job is not None:
                job()
            if self._dirty and time.monotonic() - self._saved_at >= SAVE_EVERY:
                self._save()
        except Exception as exc:  # keep learning from the next ones
            print(f"[learning] {exc}")

    def process_pending(self) -> None:
        """Runs what is queued now, in this thread (tests)."""
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                return
            self._run(job)

    def _enqueue(self, job: Callable[[], None]) -> bool:
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            print("[learning] Falling behind; skipped something to learn")
            return False

    # ---- camera thread: ignored spots and suggestions ------------------------------------------
    @staticmethod
    def still(track: Track, width: int, height: int) -> bool:
        """Has stayed where its track began, about the same size, and never showed a face."""
        return (not track.faced and track.moved[0] < STILL_SHIFT * width and track.moved[1] < STILL_SHIFT * height
                and track.resized <= STILL_RESIZE)

    def _overlapping(self, records: list[dict], camera_id: str, box: list[float]) -> dict | None:
        best, best_iou = None, SPOT_IOU
        for r in records:
            if r.get("camera_id") == camera_id and (overlap := iou(box, r["box"])) >= best_iou:
                best, best_iou = r, overlap
        return best

    def judge(self, camera_id: str, camera_name: str, frame, detections: list[Detection], tracks: dict[int, Track],
              now: float) -> None:
        """Marks still, faceless people on a learned spot as ignored, and suggests a spot for something that
        has not moved for SUGGEST_AFTER. Runs on the camera thread after the tracker."""
        self._ensure_loaded()
        height, width = frame.shape[:2]
        suggested = []
        with self._lock:
            for d in detections:
                if d.status == "known" or d.simulated or d.track_id is None:
                    continue
                track = tracks.get(d.track_id)
                if track is None or not self.still(track, width, height):
                    continue
                box = _box(d.bbox, width, height)
                spot = self._overlapping(self._spots, camera_id, box)
                if spot is not None:
                    d.ignored = True
                    spot["last_matched"], self._dirty = now, True
                elif d.status == "unknown" and now - track.first_seen >= SUGGEST_AFTER and self._new_place(camera_id, box, now):
                    suggestion = {"id": uuid.uuid4().hex[:8], "camera_id": camera_id, "box": box, "created": now,
                                  "event_id": None, "picture": None}
                    self._suggestions.append(suggestion)
                    suggested.append(suggestion)
        for suggestion in suggested:
            picture = frame.copy()
            self._enqueue(lambda s=suggestion, p=picture: self._announce(s, camera_name, p))

    def _new_place(self, camera_id: str, box: list[float], now: float) -> bool:
        """Not suggested already, and not dismissed within the last DISMISS_FOR."""
        if self._overlapping(self._suggestions, camera_id, box):
            return False
        return self._overlapping([d for d in self._dismissed if d.get("until", 0) > now], camera_id, box) is None

    def _announce(self, suggestion: dict, camera_name: str, frame) -> None:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(v * s) for v, s in zip(suggestion["box"], (w, h, w, h)))
        cv2.rectangle(frame, (x1, y1), (x2, y2), AMBER, max(2, round(w / 500)))
        if w > 960:
            frame = cv2.resize(frame, (960, int(h * 960 / w)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        minutes = int(SUGGEST_AFTER // 60)
        event_id = self.events.log(
            "SYSTEM", f"Something on {camera_name} has not moved for {minutes} minutes and shows no face. "
                      "If it is an object, confirm it on the Learning page.", "INFO", camera=camera_name,
            snapshot=buf.tobytes() if ok else None)
        picture = self.events.pictures([event_id]).get(event_id, {}).get("snapshot") if event_id else None
        with self._lock:
            suggestion["event_id"], suggestion["picture"] = event_id, picture
        self._save()

    # ---- camera thread: faces ------------------------------------------------------------------
    def offer_faces(self, camera_id: str, camera_name: str, detections: list[Detection],
                    evidence: list[FaceEvidence], tracks: dict[int, Track], threshold: float, now: float) -> None:
        """Queues learning an insider's face when every condition in the module docstring holds. Runs on
        the camera thread after the tracker, with the faces analyze() kept."""
        self._ensure_loaded()
        with self._lock:
            for key in [k for k in self._looks if k[0] == camera_id and k[1] not in tracks]:
                del self._looks[key]
            for d, ev in zip(detections, evidence):
                track = tracks.get(d.track_id) if d.track_id is not None else None
                name = d.identity
                if d.status != "known" or d.simulated or track is None or ev.name is None or ev.name != name:
                    continue  # recognised from this face, not from the tracker's memory of it
                key = (camera_id, track.id)
                self._looks[key] = self._looks.get(key, 0) + 1
                if (key in self._blocked or self._looks[key] < LEARN_AGREE
                        or set(track.votes) != {name} or track.unknown_votes  # contested
                        or not ev.quality_ok or ev.feature is None or ev.crop is None
                        or ev.score < threshold + LEARN_MARGIN):
                    continue
                if name in self._learning or now - self._learned_at.get(name, 0.0) < LEARN_EVERY:
                    continue
                closest = self.faces.similarity(name, ev.feature)
                if closest is None or closest >= LEARN_NOVEL:
                    continue  # nothing new about this look
                self._learning.add(name)
                job = (name, ev.feature.copy(), ev.crop.copy(), round(ev.score, 2), camera_id, camera_name, track.id, now)
                if not self._enqueue(lambda job=job: self._learn_face(*job)):
                    self._learning.discard(name)

    def _learn_face(self, name: str, feat: np.ndarray, crop, score: float, camera_id: str, camera_name: str,
                    track_id: int, at: float) -> None:
        try:
            if not self.settings.get().learning.improve_faces or (camera_id, track_id) in self._blocked:
                return
            closest = self.faces.similarity(name, feat)
            if closest is None or closest >= LEARN_NOVEL:
                return  # another camera learned a look like this meanwhile
            meta = {"learned": at, "camera_id": camera_id, "camera": camera_name, "track": track_id, "score": score}
            if self.faces.add_learned(name, crop, feat, meta, MAX_LEARNED) is None:
                return
            with self._lock:
                self._learned_at[name] = at
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
            self.events.log("LEARNED", f"Learned a new photo of {name} from {camera_name}", "INFO", camera=camera_name,
                            snapshot=buf.tobytes() if ok else None)
        finally:
            with self._lock:
                self._learning.discard(name)

    # ---- feedback ----------------------------------------------------------------------------
    def feedback(self, event_id: int, verdict: str | None) -> dict:
        """Records the owner's verdict on an event and learns from it. Returns what was learned, in words."""
        if verdict is not None and verdict not in VERDICTS:
            raise LearningError(f"Unknown verdict: {verdict}")
        self._ensure_loaded()
        with self._feedback:
            db = SessionLocal()
            try:
                row = db.get(SecurityEvent, event_id)
                if row is None:
                    raise KeyError(event_id)
                event = row.to_dict()
            finally:
                db.close()
            details = event["details"] or {}
            if verdict == "wrong_person" and not (event["event_type"] == "INSIDER" and details.get("insider")):
                raise LearningError("Only an event that recognised someone can be marked as the wrong person")
            if verdict in ("real", "false_alarm") and not (event["event_type"] in PEOPLE_EVENTS and details.get("people")):
                raise LearningError("Only detections, escalations and alerts with people in the picture can be marked")
            old = event["feedback"]
            if old == verdict:
                return {"id": event_id, "feedback": verdict, "message": "Nothing changed."}
            notes = [self._undo(event, old)] if old else []
            if verdict:
                notes.append(self._teach(event, verdict))
            db = SessionLocal()
            try:
                row = db.get(SecurityEvent, event_id)
                if row is not None:
                    row.feedback = verdict
                    db.commit()
            finally:
                db.close()
            self._save()
        message = " ".join(n for n in notes if n) or "Cleared."
        return {"id": event_id, "feedback": verdict, "message": message}

    def camera_name(self, camera_id: str | None) -> str | None:
        cam = self.settings.get().camera(camera_id) if camera_id else None
        return cam.name if cam else None

    def _teach(self, event: dict, verdict: str) -> str:
        if not self.settings.get().learning.learn_from_feedback:
            return "Saved. Learning from feedback is off (Settings → Learning), so nothing changed."
        details = event["details"] or {}
        people = [p for p in details.get("people", []) if not p.get("simulated")]
        if not people:
            return "Saved. This was a test intrusion, so there is nothing to learn from it."
        if verdict == "wrong_person":
            return self._forget_faces(event, details, people)
        camera_id = details.get("camera_id")
        camera = self.camera_name(camera_id)
        if verdict == "false_alarm":
            boxes = [p["box"] for p in people if p.get("status") == "unknown"]
            if camera is None:
                return "Saved. That camera has been removed, so there is no spot to learn."
            if not boxes:
                return "Saved. Nobody unrecognised is in this picture, so there is no spot to learn."
            with self._lock:
                taught = {self._teach_spot(camera_id, box, event["id"], time.time())["id"] for box in boxes}
            spots = "that spot" if len(taught) == 1 else f"those {len(taught)} spots"
            return f"Got it. Guardian will ignore {spots} on {camera} while nothing moves there."
        with self._lock:  # real: someone really was there
            boxes = [p["box"] for p in people if p.get("status") != "known"]
            removed = [s for s in self._spots if s.get("camera_id") == camera_id
                       and any(iou(box, s["box"]) >= SPOT_IOU for box in boxes)]
            if not removed:
                return "Thanks. Noted as a real detection."
            self._spots = [s for s in self._spots if s not in removed]
            self._restore[str(event["id"])] = removed
        spots = "that spot" if len(removed) == 1 else f"those {len(removed)} spots"
        return f"Thanks. Guardian stopped ignoring {spots} on {camera}, because someone really was there."

    def _teach_spot(self, camera_id: str, box: list[float], event_id: int | None, now: float) -> dict:
        """A new spot, or a spot already there that this box strengthens. Call with _lock held."""
        spot = self._overlapping(self._spots, camera_id, box)
        if spot is None:
            spot = {"id": uuid.uuid4().hex[:8], "camera_id": camera_id, "box": box, "taught": 0, "created": now,
                    "last_matched": None, "events": [], "boxes": []}
            self._spots.append(spot)
        spot["events"].append(event_id)
        spot["boxes"].append(box)
        self._settle(spot)
        return spot

    @staticmethod
    def _settle(spot: dict) -> None:
        spot["box"] = [round(float(v), 3) for v in np.mean(np.array(spot["boxes"], np.float32), axis=0)]
        spot["taught"] = len(spot["boxes"])

    def _set_aside_dir(self, event_id: int) -> Path:
        return self.dir / "set_aside" / str(event_id)

    def _forget_faces(self, event: dict, details: dict, people: list[dict]) -> str:
        """"Not <name>": sets aside the photos of them learned from this sighting."""
        name, camera_id = details["insider"], details.get("camera_id")
        tracks = {p.get("track_id") for p in people if p.get("identity") == name and p.get("track_id") is not None}
        when = _epoch(event["timestamp"])
        with self._lock:
            self._blocked |= {(camera_id, t) for t in tracks}
        files = [p["file"] for p in self.faces.learned_photos()
                 if p["name"] == name and p.get("camera_id") == camera_id
                 and (abs(p["learned"] - when) <= SAME_SIGHTING
                      or (p.get("track") in tracks and abs(p["learned"] - when) <= SAME_TRACK))]
        moved = self.faces.set_aside_learned(name, files, self._set_aside_dir(event["id"]) / name) if files else 0
        if not moved:
            return f"Noted. Guardian had not learned any photos of {name} from this sighting, so nothing needed removing."
        return f"Got it. Removed {_photos(moved)} of {name} that Guardian had learned from this sighting."

    def _undo(self, event: dict, verdict: str) -> str:
        """Takes back what a verdict taught. Works whatever learn_from_feedback is now."""
        details, key, event_id = event["details"] or {}, str(event["id"]), event["id"]
        if verdict == "false_alarm":
            with self._lock:
                changed = [s for s in self._spots if event_id in s.get("events", [])]
                for spot in changed:
                    keep = [i for i, e in enumerate(spot["events"]) if e != event_id]
                    spot["events"] = [spot["events"][i] for i in keep]
                    spot["boxes"] = [spot["boxes"][i] for i in keep]
                    if keep:
                        self._settle(spot)
                self._spots = [s for s in self._spots if s.get("boxes")]
            return "Guardian took back the spot it learned from this." if changed else ""
        if verdict == "real":
            with self._lock:
                back = [s for s in self._restore.pop(key, []) if s not in self._spots]
                self._spots.extend(back)
            if not back:
                return ""
            return "That spot is ignored again." if len(back) == 1 else f"Those {len(back)} spots are ignored again."
        folder = self._set_aside_dir(event_id)
        name = details.get("insider")
        restored = self.faces.restore_learned(name, folder / name) if name and folder.is_dir() else 0
        shutil.rmtree(folder, ignore_errors=True)
        if not restored:
            return ""
        return f"That photo of {name} is back." if restored == 1 else f"The {restored} photos of {name} are back."

    def forget_events(self) -> None:
        """The event log was cleared: event ids start again at 1, so feedback can no longer be undone."""
        self._ensure_loaded()
        with self._lock:
            for spot in self._spots:
                spot["events"] = [None] * len(spot.get("boxes", []))
            for suggestion in self._suggestions:
                suggestion["event_id"] = suggestion["picture"] = None
            self._restore = {}
        shutil.rmtree(self.dir / "set_aside", ignore_errors=True)
        self._save()

    # ---- the Learning page ---------------------------------------------------------------------
    def overview(self) -> dict:
        self._ensure_loaded()
        cfg = self.settings.get()
        names = {c.id: c.name for c in cfg.cameras}
        with self._lock:
            spots = [{"id": s["id"], "camera_id": s["camera_id"], "camera": names.get(s["camera_id"]), "box": s["box"],
                      "taught": s.get("taught", 1), "created": s.get("created"), "last_matched": s.get("last_matched"),
                      "events": [e for e in s.get("events", []) if e is not None]} for s in self._spots]
            suggestions = [dict(s) for s in self._suggestions]
        pictures = self.events.pictures([s["event_id"] for s in suggestions if s.get("event_id")])
        for s in suggestions:
            s["camera"] = names.get(s["camera_id"])
            event = pictures.get(s.get("event_id"))
            s["event"] = event if event and event.get("snapshot") == s.get("picture") and s.get("picture") else None
            s.pop("picture", None)
        return {"settings": cfg.learning.model_dump(), "spots": spots, "suggestions": suggestions,
                "faces": self.face_stats(), "feedback": self.feedback_stats(),
                "rules": {"still_minutes": int(SUGGEST_AFTER // 60), "max_learned": MAX_LEARNED,
                          "learn_every_minutes": int(LEARN_EVERY // 60)}}

    def face_stats(self) -> list[dict]:
        learned: dict[str, list[dict]] = {}
        for photo in self.faces.learned_photos():
            learned.setdefault(photo["name"], []).append(photo)
        out = []
        for insider in self.faces.list_insiders():
            photos = sorted(learned.get(insider["name"], []), key=lambda p: p["learned"], reverse=True)
            out.append({"name": insider["name"], "photos": len(insider["photos"]), "learned": len(photos),
                        "last_learned": photos[0]["learned"] if photos else None,
                        "files": [p["file"] for p in photos]})
        return out

    @staticmethod
    def feedback_stats(days: int = FEEDBACK_DAYS) -> dict:
        db = SessionLocal()
        try:
            rows = db.query(SecurityEvent.camera, SecurityEvent.feedback, func.count(SecurityEvent.id)) \
                .filter(SecurityEvent.feedback.isnot(None), SecurityEvent.timestamp >= utcnow() - timedelta(days=days)) \
                .group_by(SecurityEvent.camera, SecurityEvent.feedback).all()
        finally:
            db.close()
        cameras: dict[str, dict] = {}
        for camera, verdict, n in rows:
            entry = cameras.setdefault(camera or "", {"camera": camera, "real": 0, "false_alarm": 0, "wrong_person": 0})
            if verdict in VERDICTS:
                entry[verdict] += n
        return {"days": days, "cameras": sorted(cameras.values(), key=lambda c: (c["camera"] or "").lower())}

    def delete_spot(self, spot_id: str) -> None:
        """Removes a spot. Like a dismissal, its place is not suggested again for DISMISS_FOR, so a thing
        still standing there is not suggested again the moment the owner removed it."""
        self._ensure_loaded()
        with self._lock:
            spot = next((s for s in self._spots if s["id"] == spot_id), None)
            if spot is None:
                raise KeyError(spot_id)
            self._spots.remove(spot)
            self._dismissed.append({"camera_id": spot["camera_id"], "box": spot["box"], "until": time.time() + DISMISS_FOR})
        self._save()

    def answer(self, suggestion_id: str, accept: bool) -> dict:
        """Confirm: the place becomes an ignored spot. Dismiss: it is not suggested again for DISMISS_FOR."""
        self._ensure_loaded()
        now = time.time()
        with self._lock:
            suggestion = next((s for s in self._suggestions if s["id"] == suggestion_id), None)
            if suggestion is None:
                raise KeyError(suggestion_id)
            self._suggestions.remove(suggestion)
            if accept:
                spot = self._teach_spot(suggestion["camera_id"], suggestion["box"], None, now)
            else:
                self._dismissed.append({"camera_id": suggestion["camera_id"], "box": suggestion["box"],
                                        "until": now + DISMISS_FOR})
        self._save()
        camera = self.camera_name(suggestion["camera_id"]) or "this camera"
        if accept:
            return {"spot": spot["id"], "message": f"Got it. Guardian will ignore that spot on {camera} while nothing moves there."}
        return {"spot": None, "message": f"Dismissed. Guardian won't suggest that place on {camera} again for a day."}

    def forget_camera(self, camera_id: str, faces: bool = True) -> dict:
        """Forgets the spots and suggestions of a camera and, with faces, the photos learned from it."""
        self._ensure_loaded()
        with self._lock:
            spots = [s for s in self._spots if s["camera_id"] == camera_id]
            suggestions = [s for s in self._suggestions if s["camera_id"] == camera_id]
            self._spots = [s for s in self._spots if s["camera_id"] != camera_id]
            self._suggestions = [s for s in self._suggestions if s["camera_id"] != camera_id]
            self._dismissed = [d for d in self._dismissed if d.get("camera_id") != camera_id]
            self._restore = {k: [s for s in v if s.get("camera_id") != camera_id] for k, v in self._restore.items()}
        self._save()
        photos = 0
        if faces:
            for photo in self.faces.learned_photos():
                if photo.get("camera_id") == camera_id:
                    try:
                        self.faces.delete_photo(photo["name"], photo["file"])
                        photos += 1
                    except (FileNotFoundError, ValueError):
                        pass
        return {"spots": len(spots), "suggestions": len(suggestions), "photos": photos}


learning_service = LearningService()
