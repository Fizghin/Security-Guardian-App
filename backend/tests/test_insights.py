from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base, SecurityEvent
from services.insights_service import insights


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def add(factory, when_local: datetime, etype: str, camera="Porch", description="Unrecognised person detected",
        severity="LOW"):
    db = factory()
    ts = when_local.astimezone(timezone.utc).replace(tzinfo=None)
    db.add(SecurityEvent(timestamp=ts, event_type=etype, description=description, severity=severity, camera=camera))
    db.commit()
    db.close()


def test_grid_cameras_and_findings(session_factory):
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    for days_ago in range(1, 6):
        add(session_factory, now - timedelta(days=days_ago, hours=-6), "DETECTION")  # 18:00 most days
    add(session_factory, now - timedelta(days=2), "DETECTION", camera="Garage")
    add(session_factory, now - timedelta(days=2), "DETECTION", description="Unrecognised person detected (test)")
    add(session_factory, now - timedelta(hours=2), "ALERT", description="Owner alerted", severity="HIGH")
    out = insights(30, now=now, session_factory=session_factory)
    assert out["totals"]["incidents"] == 6, "tests don't count"
    assert sum(map(sum, out["grid"])) == 6 and out["hours"][18] == 5
    assert out["cameras"][0] == {"camera": "Porch", "incidents": 5, "alerts": 1, "sirens": 0, "sounds": 0,
                                 "insiders": 0, "offline": 0}
    assert any("18:00–19:00" in f["text"] for f in out["findings"])
    assert any("Porch sees 83%" in f["text"] for f in out["findings"])
    assert out["threat"]["score"] > 0 and "owner alert" in out["threat"]["reasons"][0]
    assert len(out["series"]) >= 30


def test_unusual_night_raises_score(session_factory):
    now = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)
    for n in range(4):
        add(session_factory, now - timedelta(hours=6, minutes=n * 10), "DETECTION")  # 02:00 last night
    out = insights(14, now=now, session_factory=session_factory)
    assert out["night"]["last_night"] == 4 and out["night"]["baseline"] == 0
    assert out["threat"]["level"] in ("elevated", "high", "low")
    assert any("night" in r for r in out["threat"]["reasons"])
    assert any(f["kind"] == "warning" and "at night" in f["text"] for f in out["findings"])


def test_empty_log(session_factory):
    out = insights(7, session_factory=session_factory)
    assert out["totals"]["incidents"] == 0 and out["threat"] == {"score": 0, "level": "calm", "reasons": []}
    assert out["findings"][0]["text"].startswith("No incidents")
