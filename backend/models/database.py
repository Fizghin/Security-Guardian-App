from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, LargeBinary, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DATABASE_URL

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utcnow() -> datetime:
    # SQLite drops tzinfo, so everything is stored as naive UTC and tagged on the way out.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    event_type = Column(String, index=True)
    description = Column(String)
    severity = Column(String, index=True)  # INFO, LOW, MEDIUM, HIGH, CRITICAL
    recording = Column(String, nullable=True)  # file name in storage/recordings
    camera = Column(String, nullable=True, index=True)  # camera name at the time of the event
    snapshot = Column(String, nullable=True)  # picture of the moment, file name in storage/snapshots
    incident = Column(String, nullable=True, index=True)  # the camera's incident, set by its brain

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": to_iso(self.timestamp),
            "event_type": self.event_type,
            "description": self.description,
            "severity": self.severity,
            "recording": self.recording,
            "camera": self.camera,
            # The picture's file name, so its URL changes with the file: ids start again at 1 after
            # the log is cleared, and a browser must not show a deleted picture it cached.
            "snapshot": self.snapshot or None,
            "incident": self.incident,
        }


class Visitor(Base):
    """A stranger whose face Guardian remembers (see services/visitor_service.py)."""
    __tablename__ = "visitors"
    __table_args__ = {"sqlite_autoincrement": True}  # a forgotten visitor's number is never given out again

    id = Column(Integer, primary_key=True)
    label = Column(String, nullable=True)  # a name given in the dashboard
    note = Column(String, nullable=True)
    first_seen = Column(DateTime, default=utcnow)
    last_seen = Column(DateTime, nullable=True, index=True)
    visits = Column(Integer, default=0)
    cameras = Column(String, default="[]")  # JSON list of camera names
    embedding = Column(LargeBinary, nullable=True)  # float32 mean of the face embeddings below


class VisitorSighting(Base):
    """One continuous appearance of a visitor on a camera (one tracked person)."""
    __tablename__ = "visitor_sightings"

    id = Column(Integer, primary_key=True)
    visitor_id = Column(Integer, index=True)
    started = Column(DateTime, index=True)
    last_seen = Column(DateTime)
    camera = Column(String, nullable=True)
    event_id = Column(Integer, nullable=True)  # the event with a picture of the moment
    # That event's picture. Event ids start again at 1 when the log is cleared, so a sighting
    # only shows the event that still has this picture.
    event_picture = Column(String, nullable=True)
    new_visit = Column(Boolean, default=False)


class VisitorFace(Base):
    """One of a visitor's best face crops, at most one per sighting."""
    __tablename__ = "visitor_faces"

    id = Column(Integer, primary_key=True)
    visitor_id = Column(Integer, index=True)
    sighting_id = Column(Integer, nullable=True)
    file = Column(String)  # in storage/visitors/<visitor_id>/
    quality = Column(Float)
    embedding = Column(LargeBinary)
    seen_at = Column(DateTime)


def init_db() -> None:
    # Also creates tables added in later versions (visitors) in an existing database.
    Base.metadata.create_all(bind=engine)
    # create_all never alters existing tables; add columns introduced after the first release.
    existing = {c["name"] for c in inspect(engine).get_columns("security_events")}
    with engine.begin() as conn:
        if "recording" not in existing:
            conn.execute(text("ALTER TABLE security_events ADD COLUMN recording VARCHAR"))
        if "camera" not in existing:
            conn.execute(text("ALTER TABLE security_events ADD COLUMN camera VARCHAR"))
        if "snapshot" not in existing:
            conn.execute(text("ALTER TABLE security_events ADD COLUMN snapshot VARCHAR"))
        if "incident" not in existing:
            conn.execute(text("ALTER TABLE security_events ADD COLUMN incident VARCHAR"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_security_events_incident ON security_events (incident)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
