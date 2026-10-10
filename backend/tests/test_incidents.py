import base64
import json
import re
import subprocess
import sys
import zipfile

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

import evidence_api
from main import DashboardPassword, PhonePortGuard, app, is_phone_path
from models import database
from services.brain_service import CameraBrain
from services.event_service import EventService
from services.incident_service import (IncidentService, check_summary, clock, facts_text, pick_keyframes,
                                       split_voice, template_summary)
from services.recording_service import RecordingLibrary
from services.settings_service import SettingsService
from services.vault_service import EvidenceVault
from test_brain import Clock, FakeAI, FakeEvents, FakeNotifier, FakeRecorder, FakeSpeaker, advance, person

JPEG = b"\xff\xd8 a picture \xff\xd9"


class FakeLLM:
    """The language model: answers with the given replies in turn."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def resolve_model(self, cfg):
        return "local-model"

    def _chat(self, cfg, model, messages, temperature=0.7, timeout=None, max_tokens=80):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def setup(tmp_path):
    database.init_db()
    rec, snap = tmp_path / "recordings", tmp_path / "snapshots"
    rec.mkdir()
    events = EventService(snap)
    events.clear()
    library = RecordingLibrary(rec)
    vault = EvidenceVault(tmp_path / "vault", recordings_dir=rec, snapshots_dir=snap)
    vault.start(library=library, events=events)
    settings = SettingsService(tmp_path / "settings.json")
    brain = CameraBrain("door", lambda: "Front door", FakeSpeaker(), FakeRecorder(), settings=settings, ai=FakeAI(),
                        notifier=FakeNotifier(), events=events, clock=Clock())
    brain.snapshot = lambda: JPEG
    llm = FakeLLM()
    service = IncidentService(settings=settings, ai=llm, vault=vault, library=library,
                              cache_file=tmp_path / "summaries.json")
    return brain, service, vault, rec, llm


def intrusion(brain):
    """A stranger who stays until the owner is alerted, then leaves."""
    brain.clock.t += 60  # well after anyone seen before
    brain.process([person()])
    advance(brain, 5, [person()])  # level 2 starts the recording
    advance(brain, 5, [person()])  # level 3 alerts the owner
    advance(brain, 11, [])  # gone for longer than clear_after
    assert not brain.incident_active


def ids(service):
    return [i["id"] for i in service.recent()]


def wait(service):
    service._executor.submit(lambda: None).result(timeout=10)


def test_brain_tags_each_incident_with_its_own_id(tmp_path):
    events = FakeEvents()
    brain = CameraBrain("door", lambda: "Front door", FakeSpeaker(), FakeRecorder(),
                        settings=SettingsService(tmp_path / "settings.json"), ai=FakeAI(), notifier=FakeNotifier(),
                        events=events, clock=Clock())
    brain.process([person("known", "Alex", face=True)])
    intrusion(brain)
    brain.process([person()])
    tags = events.incidents
    assert tags[0] == ("INSIDER", None), "events outside an incident have none"
    key = tags[1][1]
    assert tags[1][0] == "DETECTION" and re.match(r"^[0-9A-Za-z-]+$", key)
    end = tags.index(("CLEARED", key))
    assert {i for _, i in tags[1:end + 1]} == {key}, "up to and including its end"
    assert tags[end + 1][0] == "DETECTION" and tags[end + 1][1] not in (None, key), "a new incident, a new id"


def test_incidents_are_grouped_from_their_events(setup):
    brain, service, *_ = setup
    intrusion(brain)
    brain.process([person("known", "Alex", face=True)])  # after the incident: not part of it
    intrusion(brain)
    first, second = service.recent()[::-1]
    assert first["id"] != second["id"]
    assert first["camera"] == "Front door" and first["status"] == "ended" and first["peak_level"] == 3
    assert first["end_reason"] == "Person left the area" and first["people"] == {
        "unknown": 1, "insiders": [], "visitors": []}
    assert first["clips"] == ["clip.mp4"] and first["pictures"] == 4  # detection, 2 escalations, alert
    assert first["alerts"] == 1 and first["warnings"] >= 2 and first["siren"] is False
    assert service.recent(recording="clip.mp4") and not service.recent(recording="other.mp4")
    assert service.get("no-such-incident") is None and service.get("../../etc") is None


def test_report_facts_come_from_the_event_log(setup):
    brain, service, *_ = setup
    intrusion(brain)
    incident, timeline = service.get(ids(service)[0])
    kinds = [t["type"] for t in timeline]
    assert kinds[0] == "DETECTION" and kinds[-1] == "CLEARED"
    assert {"ESCALATION", "RECORDING", "VOICE", "ALERT"} <= set(kinds)
    voice = next(t for t in timeline if t["type"] == "VOICE")
    assert voice["text"] == 'Warning: "warning level 1" (written by the language model)', "no model name"
    facts = facts_text(incident, timeline)
    for line in ("Camera: Front door", "Highest level reached: 3 (Intruder)", "Owner alerts sent: 1",
                 "Siren: did not sound", "Clips recorded: 1", f"Ended: {clock(incident['end'])} (Person left the area)",
                 f"{clock(incident['start'])} Unrecognised person detected"):
        assert line in facts
    assert "via test" not in facts


def test_voice_lines_keep_only_the_words():
    assert split_voice("Please leave now. (via some-model:3b, 812 ms)") == ("Please leave now.",
                                                                           "written by the language model")
    assert split_voice("Leave (now). (pre-written line: Model reply rejected (too short))") == (
        "Leave (now).", "a pre-written line")
    assert split_voice("Hello there (typed by operator)") == ("Hello there", "typed by the owner")


def test_template_summary_states_only_the_facts(setup):
    brain, service, *_ = setup
    intrusion(brain)
    incident, timeline = service.get(ids(service)[0])
    summary = template_summary(incident)
    assert summary.startswith(f"An unrecognised person was detected on Front door at {clock(incident['start'])}.")
    assert "level 3 (Intruder)" in summary and "alerted the owner" in summary and "recorded 1 clip" in summary
    assert check_summary(summary, facts_text(incident, timeline)) is None


FACTS = """Camera: Front door
Date: Saturday 10 October 2026
Started: 03:12:04
Ended: 03:12:51 (Person left the area)
Length: 47 seconds
Highest level reached: 3 (Intruder)
Unrecognised people when first detected: 1
Recognised household members or staff: Alex
Warnings: 2
Timeline:
03:12:04 Unrecognised person detected"""
GOOD = ("An unrecognised person was detected on Front door at 03:12:04. The incident reached level 3 (Intruder) "
        "and Guardian gave 2 warnings. It ended at 03:12:51 when the person left the area, after 47 seconds.")


@pytest.mark.parametrize("text, problem", [
    (GOOD, None),
    (GOOD.replace("03:12:04", "3:12 am"), None),
    (GOOD.replace("47 seconds", "95 seconds"), "the number 95"),
    (GOOD.replace("03:12:51", "03:15:51"), "the time 3:15"),
    (GOOD.replace("Guardian gave", "Bob heard"), "'Bob'"),
    (GOOD + " The police were called.", "'police'"),
    (GOOD.replace("person left", "man fled"), "'man'"),
    (GOOD.replace("2 warnings", "five warnings"), "'five'"),
    ("Intruder. Left.", "too short"),
    (GOOD + " " + GOOD, "sentences"),
    ("- " + GOOD.replace(". ", ".\n- "), "formatting"),
    ("I can't help with that request because it involves surveillance of people.", "refusal"),
])
def test_summary_check(text, problem):
    result = check_summary(text, FACTS)
    assert (result is None) if problem is None else (problem in result)


def ended(service, brain):
    intrusion(brain)
    incident, timeline = service.get(ids(service)[0])
    return incident, facts_text(incident, timeline)


def good_summary(incident):
    return (f"An unrecognised person was detected on Front door at {clock(incident['start'])}. The incident "
            f"reached level 3 (Intruder) and Guardian alerted the owner. It ended at {clock(incident['end'])} when "
            "the person left the area.")


def test_summary_is_written_by_the_model_and_cached(setup):
    brain, service, _, _, llm = setup
    incident, facts = ended(service, brain)
    llm.replies = [good_summary(incident), good_summary(incident)]
    first = service.summary(incident, facts)
    assert first["pending"] is True and first["text"] == template_summary(incident)
    wait(service)
    done = service.summary(incident, facts)
    assert done == {"text": good_summary(incident), "source": "llm", "reason": None,
                    "written_at": done["written_at"], "pending": False}
    assert "Facts:\n" + facts in llm.calls[0][1]["content"]
    assert service.summary(incident, facts)["source"] == "llm" and len(llm.calls) == 1, "cached"
    again = IncidentService(ai=llm, vault=service.vault, cache_file=service.cache_file)
    assert again.summary(incident, facts)["text"] == good_summary(incident), "kept across restarts"
    assert service.summary(incident, facts, regenerate=True)["pending"] is True
    wait(service)
    assert len(llm.calls) == 2


def test_rejected_summary_is_retried_once_then_replaced_by_the_template(setup):
    brain, service, _, _, llm = setup
    incident, facts = ended(service, brain)
    llm.replies = ["The police arrived at 04:00. They arrested the man who broke in.", good_summary(incident)]
    assert service.write_summary(facts, "template") == (good_summary(incident), "llm", None)
    assert "That was not acceptable: it mentions the time 4:00" in llm.calls[1][-1]["content"]

    llm.replies = ["A man in a hood tried the door.", "Bob left. The end."]
    text, source, reason = service.write_summary(facts, "template")
    assert (text, source) == ("template", "template") and "rejected" in reason

    llm.replies = [httpx.ConnectError("refused")]
    assert service.write_summary(facts, "template") == ("template", "template", "The language model is not reachable")


def test_no_model_summary_while_an_incident_is_running(setup):
    brain, service, _, _, llm = setup
    incident, facts = ended(service, brain)
    service.active = lambda: {"another-incident"}
    summary = service.summary(incident, facts)
    assert summary["source"] == "template" and summary["pending"] is False and "in progress" in summary["reason"]
    brain.process([person()])  # an incident that has not ended
    service.active = lambda: {brain.incident_key}
    running, timeline = service.get(brain.incident_key)
    assert running["status"] == "ongoing"
    assert service.summary(running, facts_text(running, timeline))["reason"] == "The incident has not ended yet."
    assert not llm.calls


def test_keyframes_spread_over_the_incident():
    items = [{"n": i} for i in range(20)]
    picks = pick_keyframes(items)
    assert len(picks) == 8 and picks[0]["n"] == 0 and picks[-1]["n"] == 19
    assert pick_keyframes(items[:5]) == items[:5]


def test_report_lists_the_evidence_with_its_vault_status(setup):
    brain, service, vault, rec, _ = setup
    (rec / "clip.mp4").write_bytes(b"video" * 1000)
    (rec / "clip.json").write_text("{}")
    vault.seal_clip(rec / "clip.mp4")
    intrusion(brain)
    assert vault.wait_idle()
    report = service.report(ids(service)[0])
    assert len(report["keyframes"]) == 4 and report["vault"]["fingerprint"] == vault.fingerprint
    clip, *pictures = report["evidence"]
    assert clip["name"] == "clip.mp4" and clip["status"] == "intact" and clip["size"] == 5000
    assert len(pictures) == 4 and all(p["status"] == "intact" and p["kind"] == "picture" for p in pictures)
    (rec / "clip.mp4").write_bytes(b"VIDEO" * 1000)
    assert service.report(ids(service)[0])["evidence"][0]["status"] == "modified"


def test_evidence_package_contents_and_its_verify_script(setup, tmp_path):
    brain, service, vault, rec, _ = setup
    (rec / "clip.mp4").write_bytes(b"video" * 1000)
    (rec / "clip.json").write_text('{"camera": "Front door"}')
    vault.seal_clip(rec / "clip.mp4")
    intrusion(brain)
    assert vault.wait_idle()
    path, name = service.package(ids(service)[0])
    try:
        assert name == f"guardian-incident-{ids(service)[0]}.zip" and path.parent.name != "recordings"
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            pictures = {n for n in names if n.startswith("keyframes/")}
            assert names - pictures == {"report.html", "clips/clip.mp4", "clips/clip.json", "ledger-entries.jsonl",
                                        "public-key.pem", "VERIFY.txt"}
            assert len(pictures) == 4 and z.getinfo("clips/clip.mp4").compress_type == zipfile.ZIP_STORED
            entries = [json.loads(line) for line in z.read("ledger-entries.jsonl").decode().splitlines()]
            assert [e["kind"] for e in entries] == ["clip"] + ["picture"] * 4 + ["report"]
            page = z.read("report.html").decode()
            assert entries[-1]["sha256"] == __import__("hashlib").sha256(z.read("report.html")).hexdigest()
            assert "Front door" in page and entries[0]["sha256"] in page and "Intact" in page
            assert f"data:image/jpeg;base64,{base64.b64encode(JPEG).decode()}" in page, "self-contained"
            assert b"PRIVATE" not in z.read("public-key.pem")
            guide = z.read("VERIFY.txt").decode()
            assert vault.fingerprint in guide
            out = tmp_path / "unzipped"
            z.extractall(out)
    finally:
        path.unlink()
    script = guide[guide.index("import hashlib"):]
    (tmp_path / "verify.py").write_text(script)
    run = subprocess.run([sys.executable, "-I", str(tmp_path / "verify.py")], cwd=out, capture_output=True, text=True,
                         timeout=60)
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert len(lines) == 6 and all(line.startswith("OK") for line in lines)
    (out / "clips" / "clip.mp4").write_bytes(b"VIDEO" * 1000)
    run = subprocess.run([sys.executable, "-I", str(tmp_path / "verify.py")], cwd=out, capture_output=True, text=True,
                         timeout=60)
    assert "CHANGED 1 clip.mp4" in run.stdout


def test_old_database_gets_the_incident_column(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE security_events (id INTEGER PRIMARY KEY, timestamp DATETIME, "
                          "event_type VARCHAR, description VARCHAR, severity VARCHAR)"))
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    assert "incident" in {c["name"] for c in inspect(engine).get_columns("security_events")}
    assert "ix_security_events_incident" in {i["name"] for i in inspect(engine).get_indexes("security_events")}


# ---- API -----------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_vault_endpoints_never_reveal_the_private_key(client):
    status = client.get("/api/vault").json()
    assert len(status["fingerprint"]) == 64 and "entries" in status
    pem = client.get("/api/vault/public-key.pem")
    assert pem.status_code == 200 and "BEGIN PUBLIC KEY" in pem.text and "PRIVATE" not in pem.text
    assert "attachment" in pem.headers["content-disposition"]
    for path in ("/vault/private_key.pem", "/api/vault/private_key.pem", "/api/vault/private-key.pem"):
        assert "PRIVATE" not in client.get(path).text
    assert client.post("/api/vault/verify").json()["checking"] is True
    for _ in range(100):
        state = client.get("/api/vault/verify").json()
        if not state["checking"]:
            break
    assert state["last_check"]["broken_at"] is None


@pytest.mark.parametrize("path", [
    "/api/recordings/..%2Fsettings.json/verify",
    "/api/recordings/..%2Fvault%2Fprivate_key.pem/verify",
    "/api/recordings/clip.part.mp4/verify",
    "/api/recordings/nothing-here.mp4/verify",
    "/api/recordings/.hidden.mp4/verify",
    "/api/incidents/..%2F..%2Fvault/report",
    "/api/incidents/a%20b/report",
    "/api/incidents/nope/summary",
    "/api/incidents/nope/package.zip",
])
def test_unknown_or_outside_paths_are_not_found(client, path):
    assert client.get(path).status_code == 404


def test_incidents_endpoint_lists_and_reports(client, setup):
    brain, *_ = setup
    intrusion(brain)
    items = client.get("/api/incidents").json()["items"]
    assert items and items[0]["camera"] == "Front door"
    report = client.get(f"/api/incidents/{items[0]['id']}/report").json()
    assert report["incident"]["id"] == items[0]["id"] and report["timeline"] and "facts" in report
    assert client.get(f"/api/incidents/{items[0]['id']}/summary").json()["text"]


def test_evidence_endpoints_need_the_dashboard_password():
    inner = FastAPI()
    inner.include_router(evidence_api.router)
    inner.add_middleware(DashboardPassword, password="s3cret")
    c = TestClient(inner)
    for path in ("/api/vault", "/api/vault/public-key.pem", "/api/vault/verify", "/api/incidents",
                 "/api/recordings/a.mp4/verify", "/api/incidents/x/report", "/api/incidents/x/package.zip"):
        assert c.get(path).status_code == 401, path
        assert not is_phone_path(path)
    assert c.post("/api/vault/verify").status_code == 401
    auth = {"Authorization": "Basic " + base64.b64encode(b"guardian:s3cret").decode()}
    assert c.get("/api/vault", headers=auth).status_code == 200

    phone_port = FastAPI()
    phone_port.include_router(evidence_api.router)
    phone_port.add_middleware(PhonePortGuard, port=80)  # the test client's port
    assert TestClient(phone_port).get("/api/vault").status_code == 404
