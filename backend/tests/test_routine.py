import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from services import routine_service as routine_module
from services.brain_service import UNUSUAL_ALERT_SECONDS, CameraBrain
from services.routine_service import (HALF_LIFE_DAYS, SAVE_SECONDS, Judgement, Routine, RoutineService, _quiet_runs,
                                      cell, hour_words)
from services.settings_service import SettingsService
from test_brain import Clock, FakeAI, FakeEvents, FakeNotifier, FakeRecorder, FakeSpeaker, person

MONDAY = datetime(2026, 3, 2)  # a Monday, four weeks before the clocks go forward in Europe


@pytest.fixture
def berlin(monkeypatch):
    """Local time with daylight saving time, whatever the machine running the tests uses."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def at(*args) -> float:
    """A moment given as this computer's wall clock shows it."""
    return datetime(*args).timestamp()


def feed(routine: Routine, start: float, end: float, busy=lambda local: False, strangers=lambda local: False):
    """Watches minute by minute from `start` to `end`; `busy(local time)` says whether people were in view."""
    t = start
    while t < end:
        local = datetime.fromtimestamp(t)
        routine.saw(t + 5, people=busy(local), strangers=strangers(local))
        t += 60
    routine.tick(end + 60)


def evenings(local: datetime) -> bool:
    return 17 <= local.hour < 19


# ---- learning ------------------------------------------------------------------------------
def test_flags_become_one_minute_of_the_grid_once_the_minute_is_over():
    r = Routine()
    t = at(2026, 3, 3, 15, 0)  # Tuesday
    r.saw(t + 1, people=False, strangers=False)
    r.saw(t + 20, people=True, strangers=False)
    r.saw(t + 40, people=False, strangers=False)
    r.tick(t + 59)
    assert r.observed[1][15] == 0  # the minute isn't over yet
    r.tick(t + 61)
    assert (r.observed[1][15], r.people[1][15], r.strangers[1][15]) == (1, 1, 0)
    r.saw(t + 70, people=False, strangers=True)  # a stranger is a person too
    r.tick(t + 300)  # minutes with no picture checked (camera offline) aren't watched
    assert (r.observed[1][15], r.people[1][15], r.strangers[1][15]) == (2, 2, 1)
    assert r.dirty


def test_days_count_once_watched_for_an_hour():
    r = Routine()
    feed(r, at(2026, 3, 2, 10), at(2026, 3, 2, 10, 59))
    feed(r, at(2026, 3, 3, 10), at(2026, 3, 3, 11))
    assert r.days_watched() == 1 and len(r.days) == 2


def test_older_weeks_count_less():
    r = Routine()
    start = at(2026, 3, 2, 3)
    feed(r, start, start + 3600)
    assert r.observed[0][3] == pytest.approx(60)
    r.judge(start + HALF_LIFE_DAYS * 86400)  # applied lazily, also when only reading
    assert r.observed[0][3] == pytest.approx(30, rel=0.01)
    r.judge(start + 2 * HALF_LIFE_DAYS * 86400)
    assert r.observed[0][3] == pytest.approx(15, rel=0.01)


def test_routine_is_saved_atomically_every_few_minutes_and_loads_again(tmp_path):
    service = RoutineService(tmp_path / "routine")
    r = service.get("garden")
    feed(r, at(2026, 3, 2, 17), at(2026, 3, 2, 18), busy=evenings)
    service.flush(1000.0)
    path = tmp_path / "routine" / "garden.json"
    assert json.loads(path.read_text())["people"][0][17] == 60
    assert not list(path.parent.glob("*.tmp"))

    feed(r, at(2026, 3, 2, 18), at(2026, 3, 2, 18, 30), busy=evenings)
    service.flush(1000.0 + SAVE_SECONDS - 1)
    assert json.loads(path.read_text())["observed"][0][18] == 0  # saved only every few minutes
    service.flush(1000.0 + SAVE_SECONDS)
    assert json.loads(path.read_text())["observed"][0][18] == 30

    again = RoutineService(tmp_path / "routine").get("garden")
    assert again.people[0][17] == pytest.approx(r.people[0][17], abs=0.001) and again.observed[0][18] == 30
    assert again.days == r.days and again.decayed_at == r.decayed_at


def test_unreadable_file_starts_afresh(tmp_path):
    (tmp_path / "porch.json").write_text('{"observed": [[1, 2]]}')
    r = RoutineService(tmp_path).get("porch")
    assert r.observed[0][0] == 0 and r.days == {}


# ---- judging --------------------------------------------------------------------------------
def test_expected_smooths_over_neighbouring_hours_and_days_of_the_same_kind():
    r = Routine()
    r.days = {f"2026-03-0{d}": 600 for d in range(2, 9)}
    for day in range(7):
        for hour in range(24):
            r.observed[day][hour] = 60
    r.people[1][15] = 60  # Tuesday 15:00 always busy
    r.people[1][16] = 30  # the next hour half
    r.people[2][15] = 60  # Wednesday 15:00 busy too
    r.people[5][15] = 60  # Saturday is another kind of day: it doesn't count for Tuesday

    j = r._judge(1, 15, r.days_watched())
    # weights: 1 for the hour, 0.5 either side, 0.25 for each of the 4 other weekdays at 15:00
    assert j.expected == pytest.approx((60 + 0.5 * 30 + 0.25 * 60) / (60 * (1 + 0.5 + 0.5 + 4 * 0.25)))
    assert j.watched_minutes == 60 * 7
    sunday = r._judge(6, 15, r.days_watched())
    assert sunday.expected == pytest.approx(0.25 * 60 / (60 * 2.25))
    monday_midnight = r._judge(0, 0, r.days_watched())  # the hour before is Sunday 23:00
    assert monday_midnight.watched_minutes == 60 * 7


def test_still_learning_until_three_days_and_three_hours_around_that_time():
    r = Routine()
    feed(r, at(2026, 3, 3, 1), at(2026, 3, 3, 5))  # Tuesday 01:00-05:00, nobody there
    j = r.judge(at(2026, 3, 3, 3, 30))
    assert j.watched_minutes == pytest.approx(180, abs=1) and j.days_watched == 1
    assert not j.confident and not j.unusual  # quiet, but one day says nothing yet

    feed(r, at(2026, 3, 4, 3), at(2026, 3, 4, 4))  # an hour on two more days
    feed(r, at(2026, 3, 5, 3), at(2026, 3, 5, 4))
    j = r.judge(at(2026, 3, 3, 3, 30))
    assert j.days_watched == 3 and j.watched_minutes > 180 and j.confident and j.unusual
    assert r.judge(at(2026, 3, 10, 3, 30)).unusual  # the next Tuesday
    for hardly_watched in (r.judge(at(2026, 3, 10, 5, 30)), r.judge(at(2026, 3, 10, 22, 30))):
        assert hardly_watched.days_watched == 3 and not hardly_watched.confident and not hardly_watched.unusual


def test_quiet_hour_is_unusual_and_busy_hour_is_not_across_daylight_saving_time(berlin):
    r = Routine()
    feed(r, at(2026, 3, 2), at(2026, 4, 6), busy=evenings)  # five weeks, through the change on 29 March
    # The night the clocks go forward has no 02:00-03:00; every other hour is watched as the clock shows it
    assert cell(at(2026, 3, 29, 3, 30)) == (6, 3)
    assert r.observed[6][2] < r.observed[6][3]

    quiet = r.judge(at(2026, 4, 7, 3, 10))  # Tuesday night, after the change
    assert quiet.confident and quiet.unusual and quiet.expected == 0
    assert quiet.quiet_note("Garden") == "Garden is usually quiet on Tuesdays around 3 am"
    busy = r.judge(at(2026, 4, 7, 17, 50))
    assert busy.confident and not busy.unusual and busy.expected > 0.5
    edge = r.judge(at(2026, 4, 7, 16, 10))  # next to the busy hours, so not "nobody is usually there"
    assert edge.confident and not edge.unusual


def test_report_describes_busy_and_quiet_times():
    r = Routine()
    feed(r, at(2026, 3, 2), at(2026, 3, 9), busy=evenings)
    report = r.report(at(2026, 3, 9, 3, 30))
    assert report["days_watched"] == 7
    assert "Busiest: weekdays 17:00–19:00" in report["summary"]
    assert "Usually quiet: weekdays 19:00–17:00" in report["summary"]
    assert report["now"] == {"day": 0, "hour": 3, "expected": 0.0, "confident": True, "unusual": True}
    assert report["share"][0][17] == 1.0 and report["share"][0][3] == 0.0
    assert report["observed_minutes"][2][17] == pytest.approx(55, abs=2)  # older days count a little less
    assert report["people_minutes"][2][17] == report["observed_minutes"][2][17]
    assert len(report["confident"]) == 7 and all(len(row) == 24 for row in report["confident"])

    learning = Routine()
    feed(learning, at(2026, 3, 2, 17), at(2026, 3, 2, 19), busy=evenings)
    report = learning.report(at(2026, 3, 2, 19))
    assert report["summary"] == [] and not any(any(row) for row in report["confident"])
    assert report["share"][1][17] is None  # never watched


def test_quiet_runs_are_the_longest_ones_in_time_order():
    shares = [0.0] * 24
    for hour in (7, 17, 18, 19):
        shares[hour] = 0.5
    shares[12] = None  # not watched enough: neither quiet nor busy
    assert _quiet_runs(shares) == [(8, 12), (20, 31)]  # 20:00 to 07:00 the next morning
    assert _quiet_runs([0.0] * 24) == [(0, 24)]
    assert _quiet_runs([0.5] * 24) == []


def test_hour_words():
    assert [hour_words(h) for h in (0, 3, 12, 15)] == ["midnight", "3 am", "noon", "3 pm"]


# ---- using it --------------------------------------------------------------------------------
def unusual(day=1, hour=3) -> Judgement:
    return Judgement(day=day, hour=hour, expected=0.0, watched_minutes=600, days_watched=10)


def usual() -> Judgement:
    return Judgement(day=1, hour=17, expected=0.6, watched_minutes=600, days_watched=10)


@pytest.fixture
def brain(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    b = CameraBrain("garden", lambda: "Garden", FakeSpeaker(), FakeRecorder(), settings=settings, ai=FakeAI(),
                    notifier=FakeNotifier(), events=FakeEvents(), clock=Clock())
    b.snapshot = lambda: b"jpeg"
    return b


def detection(brain):
    return [d for t, d in brain.events.descriptions if t == "DETECTION"]


def test_detection_at_an_unusual_time_says_why(brain):
    brain.settings.update({"learning": {"unusual_activity": "log"}})
    brain.routine = lambda now: unusual()
    brain.process([person()])
    assert detection(brain) == ["Unrecognised person detected. Unusual: Garden is usually quiet on Tuesdays around 3 am."]
    assert ("DETECTION", "MEDIUM", "Garden") in brain.events.entries
    assert brain.status()["threat_level"] == 1 and brain.unusual


def test_detection_at_a_usual_time_or_while_learning_stays_low(brain):
    for judgement in (usual(), Judgement(1, 3, 0.0, 100, 2), None):
        brain.routine = lambda now: judgement
        brain.process([person()])
        assert detection(brain)[-1] == "Unrecognised person detected"
        assert [severity for t, severity, _ in brain.events.entries if t == "DETECTION"][-1] == "LOW"
        assert not brain.unusual
        brain.reset()


def test_unusual_mode_off_ignores_the_routine(brain):
    brain.settings.update({"learning": {"unusual_activity": "off"}})
    brain.routine = lambda now: unusual()
    brain.process([person()])
    assert detection(brain) == ["Unrecognised person detected"]
    assert not brain.notifier.alerts and not brain.unusual


def test_unusual_mode_log_words_it_without_alerting(brain):
    brain.settings.update({"learning": {"unusual_activity": "log"}})
    brain.routine = lambda now: unusual()
    brain.process([person()])
    assert "Unusual:" in detection(brain)[0]
    assert brain.notifier.alerts == [] and "ALERT" not in brain.events.types()


def test_unusual_mode_alert_alerts_at_once_and_at_most_every_ten_minutes(brain):
    assert brain.settings.get().learning.unusual_activity == "alert"  # the default
    brain.routine = lambda now: unusual()
    brain.process([person(simulated=False)])
    assert brain.notifier.alerts == ["Unusual activity at Garden"]
    assert brain.notifier.messages == ["Unrecognised person at Garden at an unusual time: Garden is usually quiet on "
                                       "Tuesdays around 3 am."]
    assert brain.events.types()[:3] == ["DETECTION", "VOICE", "ALERT"] and brain.threat_level == 1
    assert ("ALERT", b"jpeg") in brain.events.pictures
    assert brain.ai.requests[0].alerted is False  # the first warning came before the alert
    assert brain.context(2).alerted  # later warnings may say the owner knows

    brain.clock.t += 20
    brain.process([])  # they leave; the incident clears
    brain.clock.t += 60
    brain.process([person()])  # back a minute later: worded as unusual, but no second alert yet
    assert len(detection(brain)) == 2 and "Unusual:" in detection(brain)[1]
    assert len(brain.notifier.alerts) == 1
    brain.reset()
    brain.clock.t += UNUSUAL_ALERT_SECONDS
    brain.process([person(), person()])
    assert len(brain.notifier.alerts) == 2
    assert brain.notifier.messages[-1].startswith("2 unrecognised people at Garden at an unusual time")


def test_unusual_alert_with_no_channel_is_not_claimed(brain):
    brain.notifier.works = False
    brain.routine = lambda now: unusual()
    brain.process([person()])
    assert "Unusual:" in detection(brain)[0] and "ALERT" not in brain.events.types()
    assert not brain.context(2).alerted


def test_nothing_is_judged_while_disarmed(brain):
    brain.settings.update({"armed": False})
    calls = []
    brain.routine = lambda now: calls.append(now) or unusual()
    brain.process([person()])
    assert calls == [] and brain.notifier.alerts == [] and detection(brain) == []


def test_test_intrusion_alert_is_marked_as_a_test(brain):
    brain.routine = lambda now: unusual()
    brain.process([person(simulated=True)])
    assert detection(brain) == ["Unrecognised person detected (test). Unusual: Garden is usually quiet on Tuesdays "
                                "around 3 am."]
    assert brain.notifier.alerts == ["[TEST] Unusual activity at Garden"]


# ---- API ---------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


def test_status_reports_routine_progress(client):
    cam = client.get("/api/status").json()["cameras"][0]
    assert cam["routine"] == {"learning": True, "days_watched": 0, "days_needed": 3, "unusual_now": False}


def test_routine_api_reset_and_camera_deletion(client):
    cam_id = client.post("/api/cameras", json={"name": "Shed", "source": "none"}).json()["id"]
    routine = routine_module.routine_service.get(cam_id)
    now = time.time()
    feed(routine, now - 7 * 86400, now - 60, busy=evenings)
    routine_module.routine_service.flush(now, force=True)
    path = routine_module.ROUTINE_DIR / f"{cam_id}.json"
    assert path.is_file()

    data = client.get(f"/api/cameras/{cam_id}/routine").json()
    assert data["name"] == "Shed" and data["mode"] == "alert" and data["days_watched"] >= 6
    assert len(data["share"]) == 7 and all(len(row) == 24 for row in data["observed_minutes"])
    assert any(line.startswith("Busiest:") for line in data["summary"])
    assert data["now"]["confident"] and data["now"]["day"] == datetime.now().weekday()
    assert client.get("/api/status").json()["cameras"][-1]["routine"]["days_watched"] >= 6

    assert client.delete(f"/api/cameras/{cam_id}/routine").json() == {"ok": True}
    assert not path.exists()
    assert client.get(f"/api/cameras/{cam_id}/routine").json()["days_watched"] == 0
    events = client.get("/api/events?type=SYSTEM&limit=5").json()["items"]
    assert any(e["description"].startswith("Learned routine reset") and e["camera"] == "Shed" for e in events)

    feed(routine, now - 2 * 86400, now - 86400)
    routine_module.routine_service.flush(now, force=True)
    assert path.is_file()
    assert client.delete(f"/api/cameras/{cam_id}").status_code == 200
    assert not path.exists()
    assert client.get(f"/api/cameras/{cam_id}/routine").status_code == 404
    assert client.delete("/api/cameras/nope/routine").status_code == 404


def test_unusual_activity_setting_is_validated(client):
    assert client.get("/api/settings").json()["learning"]["unusual_activity"] == "alert"
    assert client.patch("/api/settings", json={"learning": {"unusual_activity": "loud"}}).status_code == 422
    saved = client.patch("/api/settings", json={"learning": {"unusual_activity": "log"}}).json()
    assert saved["learning"]["unusual_activity"] == "log"
    client.patch("/api/settings", json={"learning": {"unusual_activity": "alert"}})
