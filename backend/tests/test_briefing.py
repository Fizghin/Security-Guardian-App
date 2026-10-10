import time
from datetime import datetime, timedelta, timezone

import pytest

from models.database import SecurityEvent, SessionLocal, init_db
from services.ai_service import AIError
from services.briefing_service import (BriefingService, check_briefing, clean_briefing, clock, collect_facts, facts_text,
                                       template)
from services.settings_service import SettingsService

# Each test looks at its own day in 2020, far from the events other tests log now.
DAY = datetime(2020, 3, 10, 8, 0, tzinfo=timezone.utc)


def seed(now: datetime, rows: list[tuple[int, str, str, str | None]]) -> None:
    """rows: (minutes before `now`, type, description, camera)"""
    init_db()
    db = SessionLocal()
    try:
        for minutes, etype, desc, camera in rows:
            when = (now - timedelta(minutes=minutes)).astimezone(timezone.utc).replace(tzinfo=None)
            db.add(SecurityEvent(timestamp=when, event_type=etype, description=desc, severity="INFO", camera=camera))
        db.commit()
    finally:
        db.close()


BUSY_DAY = [
    (2000, "DETECTION", "Unrecognised person detected", "Porch"),  # the day before: not counted
    (1430, "CLEARED", "Person left the area. Incident lasted 60s, peak level 2.", "Porch"),  # started before
    (600, "ARMED", "System armed by schedule", None),
    (300, "DETECTION", "Unrecognised person detected", "Porch"),
    (299, "ESCALATION", "Threat level 2 (Loitering) after 5s", "Porch"),
    (298, "ESCALATION", "Threat level 3 (Intruder) after 10s", "Porch"),
    (298, "ALERT", "Owner alerted: Unrecognised person at Porch for 10s.", "Porch"),
    (296, "CLEARED", "Person left the area. Incident lasted 40s, peak level 3.", "Porch"),
    (295, "CLIP_SAVED", "Saved 30s intrusion clip (peak level 3)", "Porch"),
    (200, "DETECTION", "2 unrecognised people detected", "Garden"),
    (199, "CLEARED", "Person left the area. Incident lasted 12s, peak level 1.", "Garden"),
    (150, "DETECTION", "Unrecognised person detected (test)", "Porch"),
    (149, "ESCALATION", "Threat level 2 (Loitering) after 5s", "Porch"),
    (148, "CLEARED", "Person left the area. Incident lasted 20s, peak level 2.", "Porch"),
    (120, "INSIDER", "Recognised Sam", "Porch"),
    (60, "INSIDER", "Recognised Sam", "Garden"),
    (100, "CAMERA_OFFLINE", "Camera stopped sending video", "Garden"),
    (98, "CAMERA_ONLINE", "Video is back after 120s", "Garden"),
    (30, "DISARMED", "System disarmed: detections will not raise alarms", None),
    (20, "VOICE", "Please leave. (via a model)", "Porch"),  # not a fact the briefing uses
    (500, "SYSTEM", "Guardian stopped", None),
    (480, "SYSTEM", "Guardian started (armed, 2 camera(s))", None),
    (470, "SYSTEM", "Camera removed", "Shed"),
]


@pytest.fixture(scope="module")
def busy_day():
    seed(DAY, BUSY_DAY)
    return DAY


def test_facts_from_the_event_log(busy_day):
    facts = collect_facts(busy_day, armed_now=False)
    assert facts["counts"] == {"incidents": 3, "unknown_people": 3, "alerts": 1, "insiders": 1, "offline": 1,
                               "recordings": 1, "arming_changes": 2, "panics": 0, "tests": 1}
    assert [(i["camera"], i["peak_level"]) for i in facts["incidents"]] == [("Porch", 2), ("Porch", 3), ("Garden", 1)]
    first = facts["incidents"][0]  # started before the period: its start comes from the duration
    assert clock(first["time"]) == (busy_day - timedelta(minutes=1431)).astimezone().strftime("%H:%M")
    sam = facts["insiders"][0]
    assert sam["name"] == "Sam" and sam["cameras"] == ["Porch", "Garden"]
    assert clock(sam["first"]) == (busy_day - timedelta(minutes=120)).astimezone().strftime("%H:%M")
    assert clock(sam["last"]) == (busy_day - timedelta(minutes=60)).astimezone().strftime("%H:%M")
    assert facts["offline"] == [{"camera": "Garden", "time": facts["offline"][0]["time"], "back_after_seconds": 120}]
    assert [(a["armed"], a["by"]) for a in facts["arming"]] == [(True, "schedule"), (False, "hand")]
    assert facts["armed_at_start"] is False and facts["armed_now"] is False
    assert len(facts["starts"]) == 1 and facts["starts"][0]["stopped_at"] is not None


def test_facts_text_and_template(busy_day):
    facts = collect_facts(busy_day, armed_now=False)
    text = facts_text(facts, busy_day.astimezone())
    for expected in ("Incidents with unrecognised people: 3", "Porch at", "highest level 3 of 4 (Intruder)",
                     "Unrecognised people detected: 3", "Alerts sent to the owner: 1", "Insiders recognised: 1 (Sam, from",
                     "Garden at", "back after 2 minutes", "Video clips saved: 1", "armed by the schedule",
                     "disarmed by hand", "Test intrusions run by the owner: 1", "Guardian is disarmed now",
                     "Guardian was stopped at"):
        assert expected in text, expected
    written = template(facts)
    assert written.startswith("There were 3 incidents with unrecognised people")
    assert "You were sent 1 alert and 1 clip was saved." in written
    assert "Sam was recognised from" in written and "back 2 minutes later" in written
    assert "Guardian was armed by the schedule at" in written and "1 test intrusion was run." in written
    stopped, started = ((busy_day - timedelta(minutes=m)).astimezone().strftime("%H:%M") for m in (500, 480))
    assert written.endswith(f"; it was off from {stopped} to {started}; 1 test intrusion was run.")
    # The template passes the same check the model's replies must pass
    assert check_briefing(written, text) is None


def test_quiet_day_gets_a_short_line():
    now = DAY + timedelta(days=3)
    facts = collect_facts(now, armed_now=True)
    assert not any(facts["counts"].values())
    assert template(facts) == ("All quiet over the last 24 hours: no unrecognised people, no alerts and no cameras "
                               "offline.")
    assert "disarmed the whole time" in template(collect_facts(now, armed_now=False))
    # Guardian only says what it saw while it was running
    seed(now, [(90, "SYSTEM", "Guardian started (armed, 1 camera(s))", None)])
    since = (now - timedelta(minutes=90)).astimezone().strftime("%H:%M")
    assert template(collect_facts(now, armed_now=True)).startswith(f"All quiet since Guardian started at {since}:")


GOOD = ("There were 3 incidents with unrecognised people, and the one at Porch reached level 3. "
        "You were sent 1 alert and 1 clip was saved. Sam was recognised, and Garden was offline for 2 minutes.")


@pytest.mark.parametrize("reply, problem", [
    (GOOD, None),
    (GOOD.replace("Sam", "Bob"), "mentions 'Bob'"),
    (GOOD.replace("3 incidents", "999 incidents"), "the number 999"),
    (GOOD.replace("3 incidents", "a hundred incidents"), "'hundred'"),
    (GOOD + " The police were called.", "'police'"),
    (GOOD.replace("There were 3 incidents", "It was a quiet day, with 3 incidents"), "quiet"),
    ("I'm sorry, but I can't help with writing a security briefing about these people. Is there anything else?",
     "refusal"),
    ("Quiet day.", "too short"),
    ("- Porch: 3 incidents\n- Garden: 1 alert\n- Sam was recognised today at the Porch camera", "formatting"),
])
def test_check_briefing(busy_day, reply, problem):
    facts = facts_text(collect_facts(busy_day, armed_now=False), busy_day.astimezone())
    found = check_briefing(clean_briefing(reply), facts)
    assert (found is None) if problem is None else (problem in found)


class FakeAI:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def resolve_model(self, cfg):
        return "local-model"

    def _chat(self, cfg, model, messages, **kwargs):
        self.calls.append(list(messages))
        reply = self.replies.pop(0) if self.replies else AIError("No server")
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeNotifier:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def send_alert(self, title, message, severity="HIGH", snapshot=None):
        self.sent.append((title, message, severity))
        return self.ok


class FakeEvents:
    def __init__(self):
        self.logged = []

    def log(self, event_type, description, *args, **kwargs):
        self.logged.append((event_type, description))


def service(tmp_path, ai=None, notifier=None, settings=None) -> BriefingService:
    return BriefingService(settings=settings or SettingsService(tmp_path / "settings.json"), ai=ai or FakeAI(),
                           notifier=notifier or FakeNotifier(), events=FakeEvents(), path=tmp_path / "briefing.json")


def test_model_briefing_is_used_when_it_checks_out(tmp_path, busy_day):
    ai = FakeAI(f"**Briefing:** {GOOD}")
    svc = service(tmp_path, ai)
    briefing = svc.generate(busy_day)
    assert briefing["source"] == "llm" and briefing["text"] == GOOD and briefing["note"] is None
    assert briefing["facts"]["counts"]["incidents"] == 3 and briefing["period"]["hours"] == 24
    assert briefing["generated_at"] == "2020-03-10T08:00:00Z"
    prompt = ai.calls[0][1]["content"]
    assert "Incidents with unrecognised people: 3" in prompt and "Sam" in prompt
    # Kept on disk for the next start
    assert service(tmp_path).current()["text"] == GOOD


def test_one_retry_then_the_template(tmp_path, busy_day):
    ai = FakeAI(GOOD.replace("Sam", "Bob"), GOOD)
    briefing = service(tmp_path, ai).generate(busy_day)
    assert briefing["source"] == "llm" and briefing["text"] == GOOD
    assert "not acceptable: it mentions 'Bob'" in ai.calls[1][-1]["content"]

    ai = FakeAI(GOOD.replace("Sam", "Bob"), GOOD + " The police were called.")
    briefing = service(tmp_path, ai).generate(busy_day)
    assert briefing["source"] == "template" and len(ai.calls) == 2
    assert briefing["text"] == template(briefing["facts"]) and "rejected" in briefing["note"]

    briefing = service(tmp_path, FakeAI(AIError("Cannot reach ollama"))).generate(busy_day)
    assert briefing["source"] == "template" and briefing["note"] == "Cannot reach ollama"


def test_quiet_day_does_not_ask_the_model(tmp_path):
    ai = FakeAI(GOOD)
    briefing = service(tmp_path, ai).generate(DAY + timedelta(days=5))
    assert briefing["source"] == "template" and briefing["text"].startswith("All quiet") and ai.calls == []


def wait(svc: BriefingService) -> None:
    deadline = time.time() + 10
    while svc.generating and time.time() < deadline:
        time.sleep(0.02)
    assert not svc.generating


def local(day: int, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, h, m).astimezone()


def test_daily_briefing_runs_once_a_day_also_after_a_restart(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    settings.update({"briefing": {"time": "08:00", "send": True}})
    notifier = FakeNotifier()
    svc = service(tmp_path, notifier=notifier, settings=settings)
    assert svc.run_if_due(local(12, "07:59")) is False
    assert svc.run_if_due(local(12, "08:00")) is True
    wait(svc)
    assert [s[0] for s in notifier.sent] == ["Daily briefing"] and notifier.sent[0][1] == svc.briefing["text"]
    assert svc.events.logged == [("BRIEFING", "Daily briefing sent to your alert channels")]
    assert svc.run_if_due(local(12, "08:10")) is False and svc.run_if_due(local(12, "23:59")) is False

    restarted = service(tmp_path, notifier=notifier, settings=settings)
    assert restarted.daily_done == "2026-10-12" and restarted.briefing is not None
    assert restarted.run_if_due(local(12, "09:00")) is False
    # Off at 08:00 the next day: it runs when Guardian is back
    assert restarted.run_if_due(local(13, "11:30")) is True
    wait(restarted)
    assert len(notifier.sent) == 2


def test_daily_briefing_settings(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    settings.update({"briefing": {"enabled": False}})
    svc = service(tmp_path, settings=settings)
    assert svc.run_if_due(local(12, "09:00")) is False
    assert svc.current() == {"text": None, "source": None, "note": None, "generated_at": None, "period": None,
                             "facts": None, "generating": False, "enabled": False}

    # Not sent unless asked; without a channel it says so in the log
    settings.update({"briefing": {"enabled": True, "send": False}})
    notifier = FakeNotifier(ok=False)
    svc = service(tmp_path, notifier=notifier, settings=settings)
    assert svc.run_if_due(local(12, "09:00")) is True
    wait(svc)
    assert notifier.sent == [] and svc.briefing["text"]
    settings.update({"briefing": {"send": True}})
    svc = service(tmp_path, notifier=notifier, settings=settings)
    assert svc.run_if_due(local(13, "09:00")) is True
    wait(svc)
    assert svc.events.logged == [("BRIEFING", "Daily briefing written, but not sent: no alert channel is set up")]


def test_a_daily_run_waits_for_one_being_written(tmp_path):
    svc = service(tmp_path)
    svc.generating = True  # e.g. Refresh was just pressed
    assert svc.run_if_due(local(12, "09:00")) is False and svc.daily_done is None
    svc.generating = False
    assert svc.run_if_due(local(12, "09:00")) is True
    wait(svc)


@pytest.mark.parametrize("value", ["8:00", "24:00", "noon"])
def test_briefing_time_is_validated(tmp_path, value):
    from services.settings_service import SettingsError
    with pytest.raises(SettingsError):
        SettingsService(tmp_path / "settings.json").update({"briefing": {"time": value}})
