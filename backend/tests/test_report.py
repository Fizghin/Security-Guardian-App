import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base, SecurityEvent
from services import report_service
from services.evidence_service import EvidenceVault
from services.recording_service import RecordingLibrary

NAME = "20260101_030000_cam1_intruder.mp4"


@pytest.fixture
def env(tmp_path):
    recs = tmp_path / "rec"
    recs.mkdir()
    started = datetime.now().astimezone().replace(microsecond=0) - timedelta(hours=1)
    (recs / NAME).write_bytes(b"v" * 500)
    (recs / NAME).with_suffix(".json").write_text(json.dumps({
        "reason": "intruder", "started": started.isoformat(), "duration": 40.0, "max_level": 4,
        "camera_id": "cam1", "camera": "Porch"}))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    t0 = started.astimezone(timezone.utc).replace(tzinfo=None)
    for sec, etype, desc, sev, cam in [
        (-3, "DETECTION", "Unrecognised person detected (Visitor 3, seen before: 2 visits)", "LOW", "Porch"),
        (2, "ESCALATION", "Threat level 2 (Loitering) after 5s", "MEDIUM", "Porch"),
        (3, "VOICE", "You are being recorded. (via llama3.2)", "INFO", "Porch"),
        (7, "ESCALATION", "Threat level 3 (Intruder) after 10s", "HIGH", "Porch"),
        (7, "ALERT", "Owner alerted: someone at Porch", "HIGH", "Porch"),
        (12, "ESCALATION", "Threat level 4 (Alarm) after 15s", "CRITICAL", "Porch"),
        (12, "SIREN", "Siren sounding on this computer", "CRITICAL", "Porch"),
        (30, "CLEARED", "Person left. Incident lasted 33s, peak level 4.", "LOW", "Porch"),
        (10, "DETECTION", "Unrecognised person detected", "LOW", "Garage"),  # another camera
        (-600, "DETECTION", "Unrecognised person detected", "LOW", "Porch"),  # an earlier incident
    ]:
        db.add(SecurityEvent(timestamp=t0 + timedelta(seconds=sec), event_type=etype, description=desc,
                             severity=sev, camera=cam))
    db.commit()
    db.close()
    vault = EvidenceVault(tmp_path / "ev", recs)
    return vault, RecordingLibrary(recs), factory


def test_report_timeline_summary_and_integrity(env):
    vault, library, factory = env
    vault.seal(library.dir / NAME)
    r = report_service.build(NAME, vault=vault, library=library, session_factory=factory)
    types = [e["event_type"] for e in r["timeline"]]
    assert types == ["DETECTION", "ESCALATION", "VOICE", "ESCALATION", "ALERT", "ESCALATION", "SIREN", "CLEARED"]
    assert r["timeline"][0]["offset"] == -3.0
    s = r["stats"]
    assert s["warnings"] == 1 and s["alerts"] == 1 and s["siren"] and s["lasted"] == 33 and s["returning_visitor"]
    assert [e["level"] for e in s["escalations"]] == [2, 3, 4]
    assert "an unrecognised person was detected at Porch" in r["summary"]
    assert "escalated to level 4 (Alarm) 15 s after the first detection" in r["summary"]
    assert "spoke 1 warning, alerted the owner and sounded the siren" in r["summary"]
    assert "integrity is verified" in r["summary"] and r["integrity"]["status"] == "verified"
    page = report_service.render_html(r)
    assert "Integrity verified" in page and r["integrity"]["sha256"] in page and "<script" not in page


def test_report_escapes_and_flags_unsealed(env):
    vault, library, factory = env
    r = report_service.build(NAME, vault=vault, library=library, session_factory=factory)
    assert r["integrity"]["status"] == "unsealed" and "not sealed" in r["summary"]
    r["camera"] = "<img src=x onerror=alert(1)>"
    assert "<img src=x" not in report_service.render_html(r)
    with pytest.raises(FileNotFoundError):
        report_service.build("nope.mp4", vault=vault, library=library, session_factory=factory)
