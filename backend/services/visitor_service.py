"""
Repeat visitors: Guardian remembers strangers' faces and recognises them when
they come back.

While armed, each tracked person who counts as a stranger keeps the best look
at their face that face_service.analyze() found. That face is compared with the
remembered visitors in memory, which is quick enough for the camera thread, so
the brain can already say "seen before" when it logs the detection. Everything
that writes to the database or the disk runs on one background thread, at most
every few seconds per person. Looking for faces is the costly part: with no
insiders enrolled, people already recognised from a good look are only looked
at again every few seconds, so the camera keeps its frame rate.

A visit is a sighting more than visit_gap_minutes after the visitor was last
seen. Each visitor keeps its best face crops (one per sighting, up to
MAX_FACES) in storage/visitors/<id>/, and matches on the mean of their
embeddings. Insiders are never stored: their tracks are skipped, faces that
look like an insider are dropped, and visitors who look like a newly added
insider are removed at the next clean-up.
"""
import json
import queue
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

from config import VISITORS_DIR
from models.database import SessionLocal, Visitor, VisitorFace, VisitorSighting, to_iso, utcnow
from models.domain import Detection
from services.event_service import event_service
from services.face_service import AMBIGUITY_MARGIN, FaceError, face_service, safe_name
from services.settings_service import settings_service
from services.tracker import FaceEvidence, iou

MAX_FACES = 6
MAX_SIGHTINGS = 50  # the newest sightings kept per visitor
STRICTER = 0.04     # visitors need a closer match than insiders: a wrong match merges two people
NEW_AFTER = 2.0     # seconds a stranger's face is followed before they are remembered as a new visitor
SEND_EVERY = 5.0    # a person's better face and the time they were last seen are saved at most this often
REFRESH_EVERY = 60.0  # ...and at least this often while they stay
TRACK_END = 4.0     # a person not seen for this long has left (the tracker forgets them after 2.5 s)
GOOD_LOOK = 0.5     # a face this good is enough to know who someone is...
LOOK_AGAIN = 5.0    # ...after which they are looked at again this often, for a better photo
CROP_SIDES = (224, 400)  # crops are scaled into this range, big enough to enrol as insider photos
QUEUE_SIZE = 256


def _utc(ts: float) -> datetime:
    """Epoch seconds as stored in the database (naive UTC)."""
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _epoch(dt: datetime | None) -> float | None:
    return None if dt is None else dt.replace(tzinfo=timezone.utc).timestamp()


def _vector(blob: bytes | None) -> np.ndarray | None:
    return None if blob is None else np.frombuffer(blob, np.float32).copy()


def _clean(text: str | None, limit: int) -> str | None:
    return " ".join((text or "").split())[:limit] or None


def when(ts: float, now: float) -> str:
    """'today 23:10', 'yesterday 23:10', 'Tue 23:10' or '3 Oct 23:10', in this computer's time zone."""
    then = datetime.fromtimestamp(ts)
    days = (datetime.fromtimestamp(now).date() - then.date()).days
    if days <= 0:
        day = "today"
    elif days == 1:
        day = "yesterday"
    elif days < 7:
        day = then.strftime("%a")
    else:
        day = f"{then.day} {then:%b}"
    return f"{day} {then:%H:%M}"


def display_name(visitor_id: int, label: str | None) -> str:
    return label or f"Visitor {visitor_id}"


def _fit(crop):
    """Scales a face crop into CROP_SIDES."""
    h, w = crop.shape[:2]
    low, high = CROP_SIDES
    f = low / min(h, w) if min(h, w) < low else high / max(h, w) if max(h, w) > high else 1.0
    if f == 1.0:
        return crop
    return cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)


@dataclass
class _Known:
    """A remembered visitor, kept in memory for matching on the camera thread."""
    ref: np.ndarray
    label: str | None
    visits: int
    last_seen: float


@dataclass
class _Track:
    """A person followed by one camera's tracker who is not an insider (yet)."""
    first_seen: float
    last_seen: float
    bbox: list[float] | None = None
    camera: str = ""
    feature: np.ndarray | None = None  # the best look at their face so far
    crop: np.ndarray | None = None
    quality: float = 0.0
    face_first: float | None = None
    stranger: bool = False  # counted as unknown at some point; only then is anything saved
    visitor_id: int | None = None
    seen_before: str | None = None  # set when they matched a visitor remembered earlier
    event_id: int | None = None
    sighting_id: int | None = None  # set once saved
    queued: int = 0
    sent_at: float = 0.0
    sent_quality: float = 0.0
    sent_event: int | None = None
    ignore: bool = False  # an insider, or forgotten while in view


@dataclass
class _Job:
    track: _Track
    feature: np.ndarray
    crop: np.ndarray
    quality: float
    first_seen: float
    last_seen: float
    camera: str
    event_id: int | None


class VisitorService:
    def __init__(self, directory: Path = VISITORS_DIR, settings=settings_service, faces=face_service,
                 events=event_service):
        self.dir = directory
        self.settings, self.faces, self.events = settings, faces, events
        self._lock = threading.Lock()    # memory: tracks and the visitors to match against
        self._write = threading.RLock()  # the database and the files; taken before _lock, never inside it
        self._known: dict[int, _Known] = {}
        self._ids: list[int] = []
        self._refs = np.zeros((0, 0), np.float32)
        self._tracks: dict[tuple[str, int], _Track] = {}
        self._looked: dict[str, float] = {}  # camera id -> when faces were last looked for
        self._loaded = False
        self._queue: queue.Queue[_Job] = queue.Queue(QUEUE_SIZE)
        self._worker: threading.Thread | None = None
        self._dropped = False

    # ---- memory ------------------------------------------------------------------
    def load(self) -> None:
        db = SessionLocal()
        try:
            known = {v.id: _Known(_vector(v.embedding), v.label, v.visits or 0, _epoch(v.last_seen) or 0.0)
                     for v in db.query(Visitor).filter(Visitor.embedding.isnot(None))}
        finally:
            db.close()
        with self._lock:
            self._known = known
            self._rebuild()
            self._loaded = True

    def _rebuild(self) -> None:
        """Call with _lock held after _known changed."""
        self._ids = list(self._known)
        self._refs = np.array([k.ref for k in self._known.values()], np.float32) if self._known \
            else np.zeros((0, 0), np.float32)

    def _match(self, feat: np.ndarray, threshold: float) -> int | None:
        """The visitor this face belongs to, or None. Call with _lock held. A face nearly as close to
        a second visitor counts as no match: better a new visitor than two people merged into one."""
        if not self._ids:
            return None
        scores = self._refs @ feat
        order = np.argsort(scores)[::-1]
        best = float(scores[order[0]])
        second = float(scores[order[1]]) if len(order) > 1 else 0.0
        threshold += STRICTER
        if best >= threshold and (best - second >= AMBIGUITY_MARGIN or second < threshold):
            return self._ids[int(order[0])]
        return None

    def _note(self, visitor_id: int, now: float) -> str:
        known = self._known[visitor_id]
        visits = f"{known.visits} visit{'' if known.visits == 1 else 's'}"
        return f"{display_name(visitor_id, known.label)}, seen before: {visits}, last {when(known.last_seen, now)}"

    # ---- camera thread -------------------------------------------------------------
    def wants_look(self, camera_id: str, detections: list[Detection], now: float) -> bool:
        """Whether to look for faces in this frame, when only visitors need them (no insiders enrolled):
        yes while someone in view is still being identified, otherwise every LOOK_AGAIN seconds."""
        with self._lock:
            if detections and now - self._looked.get(camera_id, 0.0) >= LOOK_AGAIN:
                need = True
            else:
                settled = [t.bbox for (cam, _), t in self._tracks.items()
                           if cam == camera_id and t.bbox and self._settled(t, now)]
                need = any(not any(iou(d.bbox, box) > 0.3 for box in settled) for d in detections)
            if need:
                self._looked[camera_id] = now
            return need

    @staticmethod
    def _settled(t: _Track, now: float) -> bool:
        """Recognised from a good look, or a stranger for a while already (e.g. facing away)."""
        if t.ignore:
            return True
        return t.stranger and ((t.visitor_id is not None and t.quality >= GOOD_LOOK) or now - t.first_seen >= LOOK_AGAIN)

    def annotate(self, camera_id: str, detections: list[Detection], evidence: list[FaceEvidence], now: float,
                 threshold: float) -> None:
        """Keeps each stranger's best look at their face and marks the ones who are remembered
        visitors. Runs on the camera thread after the tracker and before the brain."""
        if not self._loaded:
            self.load()
        with self._lock:
            for d, ev in zip(detections, evidence):
                if d.track_id is None or d.simulated:
                    continue
                t = self._tracks.get((camera_id, d.track_id))
                if d.status == "known":
                    if t is not None:
                        t.ignore = True  # an insider is never remembered as a visitor
                    continue
                if t is None:
                    t = self._tracks[(camera_id, d.track_id)] = _Track(first_seen=now, last_seen=now)
                t.last_seen, t.bbox = now, d.bbox
                if t.ignore:
                    continue
                if ev.feature is not None and ev.crop is not None and ev.quality > t.quality:
                    t.feature, t.crop, t.quality = ev.feature, ev.crop.copy(), ev.quality
                    if t.face_first is None:
                        t.face_first = now
                    if t.visitor_id is None and not t.queued:
                        t.visitor_id = self._match(t.feature, threshold)
                        if t.visitor_id is not None:
                            t.seen_before = self._note(t.visitor_id, now)
                if d.status == "unknown" and t.visitor_id is not None:
                    known = self._known.get(t.visitor_id)
                    d.visitor_id, d.seen_before = t.visitor_id, t.seen_before
                    d.visitor_label = known.label if known else None

    def record(self, camera_id: str, camera_name: str, detections: list[Detection], now: float,
               event_id: int | None) -> None:
        """Queues saving the strangers in view, rate-limited per person, and those who left. Runs on the
        camera thread after the brain; event_id is its latest event with a picture."""
        jobs = []
        with self._lock:
            for d in detections:
                if d.status != "unknown" or d.track_id is None or d.simulated:
                    continue
                t = self._tracks.get((camera_id, d.track_id))
                if t is None or t.ignore or t.feature is None:
                    continue
                t.stranger, t.camera = True, camera_name
                if t.event_id is None:
                    t.event_id = event_id
                if t.queued or now - t.sent_at < SEND_EVERY:
                    continue
                if t.visitor_id is None:
                    due = now - t.face_first >= NEW_AFTER  # a few looks first, to keep a good face
                else:
                    due = (t.sighting_id is None or t.quality > t.sent_quality or t.event_id != t.sent_event
                           or now - t.sent_at >= REFRESH_EVERY)
                if due:
                    jobs.append(self._job(t, now))
            jobs += self._expire(now)
        for job in jobs:
            self._enqueue(job)

    def expire(self, now: float) -> None:
        """Saves and forgets the people who left, also when no camera is checking for visitors."""
        with self._lock:
            jobs = self._expire(now)
        for job in jobs:
            self._enqueue(job)

    def _expire(self, now: float) -> list[_Job]:
        jobs = []
        for key, t in list(self._tracks.items()):
            if now - t.last_seen > TRACK_END:
                del self._tracks[key]
                if t.stranger and not t.ignore and t.feature is not None:
                    jobs.append(self._job(t, now))  # when they were last seen
        return jobs

    @staticmethod
    def _job(t: _Track, now: float) -> _Job:
        t.queued += 1
        t.sent_at, t.sent_quality, t.sent_event = now, t.quality, t.event_id
        return _Job(t, t.feature, t.crop, t.quality, t.first_seen, t.last_seen, t.camera, t.event_id)

    def _enqueue(self, job: _Job) -> None:
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            with self._lock:
                job.track.queued -= 1
            if not self._dropped:
                self._dropped = True
                print("[visitors] Saving visitors is falling behind; some sightings were skipped")

    # ---- background saving -----------------------------------------------------------
    def start(self) -> None:
        """Loads the remembered visitors and starts saving sightings in the background."""
        self.load()
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, daemon=True, name="visitors")
            self._worker.start()

    def _work(self) -> None:
        while True:
            self._run(self._queue.get())

    def process_pending(self) -> None:
        """Saves what is queued now, in this thread (tests)."""
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                return
            self._run(job)

    def _run(self, job: _Job) -> None:
        try:
            self._save(job)
        except Exception as exc:  # keep saving the next ones
            print(f"[visitors] Could not save a sighting: {exc}")
        finally:
            with self._lock:
                job.track.queued -= 1
            self._queue.task_done()

    def _save(self, job: _Job) -> None:
        t = job.track
        det = self.settings.get().detection
        if self.faces.closest_insider(job.feature)[1] >= det.face_match_threshold:
            with self._lock:
                t.ignore = True  # looks like an insider: never remembered
            return
        picture = self.events.pictures([job.event_id]).get(job.event_id, {}).get("snapshot") if job.event_id else None
        written, obsolete = [], []
        with self._write:
            with self._lock:
                if t.ignore:
                    return
                visitor_id = t.visitor_id if t.visitor_id is not None else \
                    self._match(job.feature, det.face_match_threshold)  # someone may have just added them
            db = SessionLocal()
            try:
                visitor = db.get(Visitor, visitor_id) if visitor_id is not None else None
                if visitor is None and t.visitor_id is not None:
                    with self._lock:
                        t.ignore = True  # forgotten while in view
                    return
                if visitor is None:
                    visitor = Visitor(first_seen=_utc(job.first_seen), visits=0, cameras="[]")
                    db.add(visitor)
                    db.flush()
                sighting = db.get(VisitorSighting, t.sighting_id) if t.sighting_id else None
                if sighting is None:
                    last = _epoch(visitor.last_seen)
                    new_visit = last is None or job.first_seen - last > det.visit_gap_minutes * 60
                    visitor.visits = (visitor.visits or 0) + int(new_visit)
                    sighting = VisitorSighting(visitor_id=visitor.id, started=_utc(job.first_seen), camera=job.camera,
                                               new_visit=new_visit)
                    db.add(sighting)
                    db.flush()
                    self._trim_sightings(db, visitor.id)
                sighting.last_seen = _utc(job.last_seen)
                if job.event_id and sighting.event_id is None and picture:
                    sighting.event_id, sighting.event_picture = job.event_id, picture
                if visitor.last_seen is None or visitor.last_seen < sighting.last_seen:
                    visitor.last_seen = sighting.last_seen
                cameras = json.loads(visitor.cameras or "[]")
                if job.camera and job.camera not in cameras:
                    visitor.cameras = json.dumps(cameras + [job.camera])
                added, removed = self._offer_face(db, visitor.id, sighting.id, job)
                written += added
                obsolete += removed
                db.flush()
                ref = self._reference(db, visitor.id)
                if ref is not None:
                    visitor.embedding = ref.tobytes()
                vid, sid = visitor.id, sighting.id
                known = _Known(ref, visitor.label, visitor.visits, _epoch(visitor.last_seen)) if ref is not None else None
                db.commit()
            except Exception:
                db.rollback()
                for path in written:
                    path.unlink(missing_ok=True)
                raise
            finally:
                db.close()
            for path in obsolete:
                path.unlink(missing_ok=True)
            with self._lock:
                t.visitor_id, t.sighting_id = vid, sid
                if known is not None:
                    self._known[vid] = known
                    self._rebuild()

    def _offer_face(self, db, visitor_id: int, sighting_id: int, job: _Job) -> tuple[list[Path], list[Path]]:
        """Keeps this sighting's face if it is among the visitor's best. Returns (files written, files
        to delete once saved)."""
        faces = db.query(VisitorFace).filter(VisitorFace.visitor_id == visitor_id).all()
        mine = next((f for f in faces if f.sighting_id == sighting_id), None)
        drop = mine
        if mine is not None and job.quality <= mine.quality:
            return [], []
        if mine is None and len(faces) >= MAX_FACES:
            drop = min(faces, key=lambda f: f.quality)
            if job.quality <= drop.quality:
                return [], []
        ok, buf = cv2.imencode(".jpg", _fit(job.crop), [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            return [], []
        folder = self.dir / str(int(visitor_id))
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex[:12]}.jpg"
        (folder / name).write_bytes(buf.tobytes())
        obsolete = []
        if drop is not None:
            obsolete.append(folder / drop.file)
            db.delete(drop)
        db.add(VisitorFace(visitor_id=visitor_id, sighting_id=sighting_id, file=name, quality=job.quality,
                           embedding=job.feature.astype(np.float32).tobytes(), seen_at=_utc(job.last_seen)))
        return [folder / name], obsolete

    @staticmethod
    def _reference(db, visitor_id: int) -> np.ndarray | None:
        """The mean of the visitor's face embeddings, normalised."""
        feats = [_vector(f.embedding) for f in db.query(VisitorFace).filter(VisitorFace.visitor_id == visitor_id)]
        if not feats:
            return None
        mean = np.mean(feats, axis=0)
        return (mean / (np.linalg.norm(mean) + 1e-9)).astype(np.float32)

    @staticmethod
    def _trim_sightings(db, visitor_id: int) -> None:
        old = db.query(VisitorSighting).filter(VisitorSighting.visitor_id == visitor_id) \
            .order_by(VisitorSighting.started.desc(), VisitorSighting.id.desc()).offset(MAX_SIGHTINGS).all()
        for row in old:
            db.delete(row)

    # ---- dashboard ------------------------------------------------------------------
    def _file(self, visitor_id: int, file: str | None) -> Path | None:
        if not file or Path(file).name != file:
            return None
        path = self.dir / str(int(visitor_id)) / file
        return path if path.is_file() else None

    @staticmethod
    def _view(v: Visitor, faces: list[VisitorFace]) -> dict:
        return {
            "id": v.id,
            "name": display_name(v.id, v.label),
            "label": v.label,
            "note": v.note or "",
            "first_seen": to_iso(v.first_seen),
            "last_seen": to_iso(v.last_seen or v.first_seen),
            "visits": v.visits or 0,
            "cameras": json.loads(v.cameras or "[]"),
            # File names, best first; a face is fetched by its place in this list
            "faces": [f.file for f in sorted(faces, key=lambda f: (-f.quality, f.id))],
        }

    def list(self, repeat: bool = False, camera: str = "", search: str = "") -> list[dict]:
        """Repeat visitors first, then the most recently seen."""
        db = SessionLocal()
        try:
            visitors = db.query(Visitor).all()
            faces: dict[int, list[VisitorFace]] = {}
            for f in db.query(VisitorFace):
                faces.setdefault(f.visitor_id, []).append(f)
        finally:
            db.close()
        items = [self._view(v, faces.get(v.id, [])) for v in visitors]
        if repeat:
            items = [i for i in items if i["visits"] > 1]
        if camera:
            items = [i for i in items if camera in i["cameras"]]
        if search:
            needle = search.lower()
            items = [i for i in items if needle in i["name"].lower() or needle in i["note"].lower()]
        return sorted(items, key=lambda i: (i["visits"] > 1, i["last_seen"] or ""), reverse=True)

    def get(self, visitor_id: int) -> dict:
        """The visitor with their sightings, newest first. A sighting's event is the one from the log
        with a picture of the moment, at /api/events/<id>/snapshot.jpg?v=<its snapshot>."""
        db = SessionLocal()
        try:
            v = db.get(Visitor, visitor_id)
            if v is None:
                raise KeyError(visitor_id)
            faces = db.query(VisitorFace).filter(VisitorFace.visitor_id == visitor_id).all()
            sightings = db.query(VisitorSighting).filter(VisitorSighting.visitor_id == visitor_id) \
                .order_by(VisitorSighting.started.desc(), VisitorSighting.id.desc()).all()
            item = self._view(v, faces)
        finally:
            db.close()
        events = self.events.pictures([s.event_id for s in sightings if s.event_id])
        item["sightings"] = []
        for s in sightings:
            event = events.get(s.event_id)
            if event is not None and (not s.event_picture or event["snapshot"] != s.event_picture):
                event = None  # the log was cleared, or the picture deleted, since
            item["sightings"].append({"id": s.id, "started": to_iso(s.started), "last_seen": to_iso(s.last_seen),
                                      "camera": s.camera, "new_visit": bool(s.new_visit), "event": event})
        return item

    def photo_path(self, visitor_id: int, n: int, version: str = "") -> Path:
        """The visitor's n-th best face. version: its file name, so a URL never shows a newer face."""
        db = SessionLocal()
        try:
            files = [f.file for f in db.query(VisitorFace).filter(VisitorFace.visitor_id == visitor_id)
                     .order_by(VisitorFace.quality.desc(), VisitorFace.id)]
        finally:
            db.close()
        path = self._file(visitor_id, files[n]) if 0 <= n < len(files) else None
        if path is None or (version and version != path.name):
            raise FileNotFoundError(f"{visitor_id}/{n}")
        return path

    def update(self, visitor_id: int, changes: dict) -> dict:
        """changes: label and/or note; empty clears them."""
        with self._write:
            db = SessionLocal()
            try:
                v = db.get(Visitor, visitor_id)
                if v is None:
                    raise KeyError(visitor_id)
                if "label" in changes:
                    v.label = _clean(changes["label"], 40)
                if "note" in changes:
                    v.note = _clean(changes["note"], 300)
                label = v.label
                db.commit()
            finally:
                db.close()
            with self._lock:
                if visitor_id in self._known:
                    self._known[visitor_id].label = label
        return self.get(visitor_id)

    def _delete(self, visitor_id: int) -> None:
        """Call with _write held."""
        db = SessionLocal()
        try:
            for table in (VisitorFace, VisitorSighting):
                db.query(table).filter(table.visitor_id == visitor_id).delete()
            db.query(Visitor).filter(Visitor.id == visitor_id).delete()
            db.commit()
        finally:
            db.close()
        shutil.rmtree(self.dir / str(int(visitor_id)), ignore_errors=True)
        with self._lock:
            self._known.pop(visitor_id, None)
            self._rebuild()
            for t in self._tracks.values():
                if t.visitor_id == visitor_id:
                    t.ignore = True  # not remembered again while still in view

    def forget(self, visitor_id: int) -> None:
        with self._write:
            db = SessionLocal()
            try:
                if db.get(Visitor, visitor_id) is None:
                    raise KeyError(visitor_id)
            finally:
                db.close()
            self._delete(visitor_id)

    def forget_all(self) -> int:
        with self._write:
            db = SessionLocal()
            try:
                db.query(VisitorFace).delete()
                db.query(VisitorSighting).delete()
                n = db.query(Visitor).delete()
                db.commit()
            finally:
                db.close()
            for folder in self.dir.iterdir():
                if folder.is_dir():
                    shutil.rmtree(folder, ignore_errors=True)
            with self._lock:
                self._known.clear()
                self._rebuild()
                for t in self._tracks.values():
                    t.ignore = True
        return n

    def make_insider(self, visitor_id: int, name: str) -> dict:
        """Enrols the visitor's best faces as photos of the insider `name`, then forgets the visitor."""
        name = safe_name(name)
        with self._write:
            db = SessionLocal()
            try:
                v = db.get(Visitor, visitor_id)
                if v is None:
                    raise KeyError(visitor_id)
                title = display_name(v.id, v.label)
                files = [f.file for f in db.query(VisitorFace).filter(VisitorFace.visitor_id == visitor_id)
                         .order_by(VisitorFace.quality.desc())]
            finally:
                db.close()
            added, problems = 0, []
            for file in files:
                path = self._file(visitor_id, file)
                if path is None:
                    continue
                try:
                    self.faces.add_photo(name, path.read_bytes())
                    added += 1
                except FaceError as exc:
                    problems.append(str(exc))
            if not added:
                raise FaceError("None of the photos could be used" + (f": {problems[0]}" if problems else ""))
            self._delete(visitor_id)
        self.events.log("INSIDER", f"Added {title} as insider {name} ({added} photo{'s' if added > 1 else ''})")
        return {"name": name, "added": added, "skipped": len(problems)}

    def prune(self) -> int:
        """Forgets visitors not seen for visitor_retention_days (0 keeps them) and any who look like an
        insider, e.g. someone added as an insider afterwards. Also deletes folders no visitor uses."""
        det = self.settings.get().detection
        cutoff = utcnow() - timedelta(days=det.visitor_retention_days) if det.visitor_retention_days > 0 else None
        removed = 0
        with self._write:
            db = SessionLocal()
            try:
                rows = db.query(Visitor.id, Visitor.first_seen, Visitor.last_seen, Visitor.embedding).all()
            finally:
                db.close()
            kept = set()
            for vid, first, last, embedding in rows:
                old = cutoff is not None and (last or first) < cutoff
                ref = _vector(embedding)
                insider = ref is not None and self.faces.closest_insider(ref)[1] >= det.face_match_threshold
                if old or insider:
                    self._delete(vid)
                    removed += 1
                else:
                    kept.add(str(vid))
            for folder in self.dir.iterdir():
                if folder.is_dir() and folder.name not in kept:
                    shutil.rmtree(folder, ignore_errors=True)
        return removed


visitor_service = VisitorService()
