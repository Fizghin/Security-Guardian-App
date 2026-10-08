from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, create_engine, inspect, text
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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": to_iso(self.timestamp),
            "event_type": self.event_type,
            "description": self.description,
            "severity": self.severity,
            "recording": self.recording,
            "camera": self.camera,
            "snapshot": bool(self.snapshot),
        }


def init_db() -> None:
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
