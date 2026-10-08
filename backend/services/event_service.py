import csv
import io
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from models.database import SecurityEvent, SessionLocal, to_iso, utcnow

SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class EventService:
    def __init__(self):
        self._lock = threading.Lock()

    def log(self, event_type: str, description: str, severity: str = "INFO", recording: str | None = None,
            camera: str | None = None) -> None:
        severity = severity.upper() if severity.upper() in SEVERITIES else "INFO"
        print(f"[event] {severity:<8} {event_type}{f' [{camera}]' if camera else ''}: {description}")
        with self._lock:
            db = SessionLocal()
            try:
                db.add(SecurityEvent(
                    timestamp=utcnow(),
                    event_type=event_type,
                    description=description,
                    severity=severity,
                    recording=recording,
                    camera=camera,
                ))
                db.commit()
            except Exception as exc:
                db.rollback()
                print(f"[event] Failed to store event: {exc}")
            finally:
                db.close()

    def _filtered(self, db, event_type=None, severity=None, since=None, until=None, search=None, camera=None):
        q = db.query(SecurityEvent)
        if camera:
            q = q.filter(SecurityEvent.camera == camera)
        if event_type:
            q = q.filter(SecurityEvent.event_type == event_type)
        if severity:
            q = q.filter(SecurityEvent.severity == severity.upper())
        if since:
            q = q.filter(SecurityEvent.timestamp >= _parse_time(since))
        if until:
            q = q.filter(SecurityEvent.timestamp <= _parse_time(until))
        if search:
            q = q.filter(SecurityEvent.description.ilike(f"%{search}%"))
        return q

    def query(self, limit=50, offset=0, **filters) -> dict:
        db = SessionLocal()
        try:
            q = self._filtered(db, **filters)
            total = q.count()
            rows = q.order_by(SecurityEvent.timestamp.desc(), SecurityEvent.id.desc()).offset(offset).limit(limit).all()
            return {"total": total, "items": [r.to_dict() for r in rows]}
        finally:
            db.close()

    def summary(self, hours: int) -> dict:
        """Event counts bucketed per hour (<= 48h) or per day, plus totals by type and severity."""
        now = utcnow()
        since = now - timedelta(hours=hours)
        hourly = hours <= 48
        step = timedelta(hours=1) if hourly else timedelta(days=1)
        if hourly:
            start = since.replace(minute=0, second=0, microsecond=0)
        else:
            start = since.replace(hour=0, minute=0, second=0, microsecond=0)

        buckets: dict[datetime, Counter] = {}
        t = start
        while t <= now:
            buckets[t] = Counter()
            t += step

        db = SessionLocal()
        try:
            rows = db.query(SecurityEvent.timestamp, SecurityEvent.event_type, SecurityEvent.severity) \
                .filter(SecurityEvent.timestamp >= since).all()
            all_types = [r[0] for r in db.query(SecurityEvent.event_type).distinct().all() if r[0]]
            all_cameras = [r[0] for r in db.query(SecurityEvent.camera).distinct().all() if r[0]]
        finally:
            db.close()

        by_type, by_severity = Counter(), Counter()
        for ts, etype, sev in rows:
            by_type[etype] += 1
            by_severity[sev] += 1
            if hourly:
                key = ts.replace(minute=0, second=0, microsecond=0)
            else:
                key = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            if key in buckets:
                buckets[key][sev] += 1

        return {
            "hours": hours,
            "bucket": "hour" if hourly else "day",
            "total": len(rows),
            "series": [
                {"start": to_iso(k), "total": sum(c.values()), **{s: c.get(s, 0) for s in SEVERITIES}}
                for k, c in buckets.items()
            ],
            "by_type": dict(by_type.most_common()),
            "by_severity": {s: by_severity.get(s, 0) for s in SEVERITIES},
            "types": sorted(all_types),
            "cameras": sorted(all_cameras),
        }

    def export_csv(self, **filters) -> str:
        db = SessionLocal()
        try:
            rows = self._filtered(db, **filters).order_by(SecurityEvent.timestamp.desc()).all()
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["id", "timestamp_utc", "camera", "type", "severity", "description", "recording"])
            for r in rows:
                writer.writerow([r.id, to_iso(r.timestamp), r.camera or "", r.event_type, r.severity, r.description,
                                 r.recording or ""])
            return buf.getvalue()
        finally:
            db.close()

    def count_since(self, since: datetime) -> int:
        db = SessionLocal()
        try:
            return db.query(func.count(SecurityEvent.id)).filter(SecurityEvent.timestamp >= since).scalar() or 0
        finally:
            db.close()

    def clear(self) -> int:
        db = SessionLocal()
        try:
            n = db.query(SecurityEvent).delete()
            db.commit()
            return n
        finally:
            db.close()


event_service = EventService()
