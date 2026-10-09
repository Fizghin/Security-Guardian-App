import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.camera_service import CameraManager
from services.schedule_service import armed_at, events, last_event, next_change, time_zone
from services.settings_service import ScheduleRule, SettingsError, SettingsService

# 2026-10-05 is a Monday
MON, SAT, SUN = 5, 10, 11
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def at(day: int, hhmm: str, month: int = 10, year: int = 2026) -> datetime:
    """A wall-clock time on this computer, as an aware moment."""
    h, m = map(int, hhmm.split(":"))
    return datetime(year, month, day, h, m).astimezone()


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc).astimezone()


NIGHTS = [ScheduleRule(days=EVERY_DAY, start="22:00", end="07:00")]
WORKDAYS = [ScheduleRule(days=[0, 1, 2, 3, 4], start="08:00", end="18:00")]


@pytest.mark.parametrize("rules, when, armed", [
    (WORKDAYS, at(MON, "08:00"), True),
    (WORKDAYS, at(MON, "17:59"), True),
    (WORKDAYS, at(MON, "18:00"), False),
    (WORKDAYS, at(SAT, "12:00"), False),
    (NIGHTS, at(MON, "23:30"), True),
    (NIGHTS, at(MON, "06:59"), True),   # started Sunday night
    (NIGHTS, at(MON, "07:00"), False),
    ([ScheduleRule(days=[6], start="22:00", end="07:00")], at(MON, "03:00"), True),   # Sunday night into Monday
    ([ScheduleRule(days=[6], start="22:00", end="07:00")], at(SUN, "03:00"), False),  # Saturday night isn't listed
])
def test_armed_at(rules, when, armed):
    assert armed_at(rules, when) is armed


def test_next_change():
    assert next_change(WORKDAYS, at(MON, "09:00"), armed=True) == at(MON, "18:00")
    assert next_change(WORKDAYS, at(SAT, "09:00"), armed=False) == at(MON + 7, "08:00")  # Monday morning
    assert next_change(NIGHTS, at(MON, "12:00"), armed=False) == at(MON, "22:00")
    assert next_change(NIGHTS, at(MON, "23:00"), armed=True) == at(MON + 1, "07:00")
    # Disarmed by hand during a period: the next change is the next start, not this period's end
    assert next_change(NIGHTS, at(MON, "23:00"), armed=False) == at(MON + 1, "22:00")
    # Back-to-back periods don't disarm in between
    joined = WORKDAYS + [ScheduleRule(days=[0, 1, 2, 3, 4], start="18:00", end="20:00")]
    assert next_change(joined, at(MON, "09:00"), armed=True) == at(MON, "20:00")
    assert next_change([], at(MON, "09:00"), armed=False) is None


def test_every_period_start_is_an_arm_event():
    joined = WORKDAYS + [ScheduleRule(days=[0, 1, 2, 3, 4], start="18:00", end="20:00")]
    monday = [(when, arms) for when, arms in events(joined, at(MON, "12:00")) if when.day == MON]
    assert monday == [(at(MON, "08:00"), True), (at(MON, "18:00"), True), (at(MON, "20:00"), False)]
    assert last_event(joined, at(MON, "19:00")) == (at(MON, "18:00"), True)
    assert last_event([], at(MON, "19:00")) is None


def test_time_zone():
    zone = time_zone(utc(2026, 10, 5, 12, 0))
    assert zone["name"] and len(zone["utc_offset"]) == 6 and zone["utc_offset"][0] in "+-"


@pytest.mark.parametrize("rule", [
    {"days": [], "start": "08:00", "end": "09:00"},
    {"days": [7], "start": "08:00", "end": "09:00"},
    {"days": [0], "start": "08:00", "end": "08:00"},
    {"days": [0], "start": "8:00", "end": "09:00"},
    {"days": [0], "start": "24:00", "end": "09:00"},
])
def test_invalid_rules_are_rejected(tmp_path, rule):
    with pytest.raises(SettingsError):
        SettingsService(tmp_path / "settings.json").update({"schedule": {"rules": [rule]}})


# ---- daylight saving time ------------------------------------------------------------------
@pytest.fixture
def new_york():
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        if time.tzname != ("EST", "EDT"):
            pytest.skip("time zone data not installed")
        yield
    finally:
        if old is None:
            os.environ.pop("TZ")
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_a_skipped_start_arms_when_the_clocks_jump(new_york):
    # 2027-03-14: the clocks jump from 02:00 EST to 03:00 EDT
    rules = [ScheduleRule(days=EVERY_DAY, start="02:30", end="06:00")]
    jump = utc(2027, 3, 14, 7, 0)
    assert next_change(rules, at(14, "00:00", month=3, year=2027), armed=False) == jump
    assert last_event(rules, jump) == (jump, True)
    # A period wholly inside the skipped hour doesn't happen that day, and isn't announced
    inside = [ScheduleRule(days=EVERY_DAY, start="02:15", end="02:45")]
    assert next_change(inside, at(14, "00:00", month=3, year=2027), armed=False) == at(15, "02:15", month=3, year=2027)


def test_the_repeated_hour_has_no_events(new_york):
    # 2026-11-01: the clocks go back from 02:00 EDT to 01:00 EST, so 01:00-02:00 happens twice
    rules = [ScheduleRule(days=EVERY_DAY, start="22:00", end="01:30")]
    first_end = utc(2026, 11, 1, 5, 30)  # 01:30 EDT
    repeated = utc(2026, 11, 1, 6, 10)   # 01:10 EST
    assert last_event(rules, repeated) == (first_end, False)
    assert armed_at(rules, repeated) is False
    assert next_change(rules, repeated, armed=False) == at(1, "22:00", month=11)


# ---- the manager ---------------------------------------------------------------------------
class Events:
    def __init__(self):
        self.logged = []

    def log(self, event_type, description, *args, **kwargs):
        self.logged.append((event_type, description))


def new_manager(tmp_path) -> CameraManager:
    """A manager as after a (re)start: settings and the schedule's memory come from tmp_path."""
    return CameraManager(settings=SettingsService(tmp_path / "settings.json"), events=Events(),
                         schedule_file=tmp_path / "schedule_state.json")


@pytest.fixture
def manager(tmp_path):
    m = new_manager(tmp_path)
    m.settings.update({"armed": False})
    return m


def enable(manager, rules, now=None):
    manager.settings.update({"schedule": {"enabled": True, "rules": [r.model_dump() for r in rules]}})
    if now:
        manager.check_schedule(now)


def armed(manager) -> bool:
    return manager.settings.get().armed


def by_schedule(manager) -> list[str]:
    return [d for _, d in manager.events.logged if "by schedule" in d]


def add_unit(manager, threat_level=0):
    brain = SimpleNamespace(threat_level=threat_level, manual=False, disarm=lambda: None, reset=lambda: False)
    manager.units["cam1"] = SimpleNamespace(brain=brain)
    return brain


def test_schedule_arms_and_disarms_at_its_times(manager):
    enable(manager, WORKDAYS)
    manager.check_schedule(at(MON, "07:00"))
    assert not armed(manager)
    manager.check_schedule(at(MON, "08:00"))
    assert armed(manager)
    assert manager.events.logged[-1] == ("ARMED", "System armed by schedule")
    manager.check_schedule(at(MON, "18:00"))
    assert not armed(manager)
    assert manager.events.logged[-1][1].startswith("System disarmed by schedule")


def test_manual_change_lasts_until_the_next_change(manager):
    enable(manager, WORKDAYS)
    manager.check_schedule(at(MON, "09:00"))
    assert armed(manager)
    manager.set_armed(False)               # by hand, during an armed period
    manager.check_schedule(at(MON, "12:00"))
    assert not armed(manager), "the schedule must not undo a manual disarm"
    manager.check_schedule(at(MON, "18:00"))
    manager.check_schedule(at(MON + 1, "08:00"))
    assert armed(manager), "the next period arms again"


@pytest.mark.parametrize("now, by_hand", [(at(MON, "20:00"), True), (at(MON, "12:00"), False)])
def test_a_restart_keeps_a_manual_change(tmp_path, now, by_hand):
    manager = new_manager(tmp_path)
    enable(manager, WORKDAYS, now)
    assert armed(manager) is not by_hand
    manager.set_armed(by_hand)
    restarted = new_manager(tmp_path)
    restarted.check_schedule(now + timedelta(minutes=5))
    assert armed(restarted) is by_hand
    assert by_schedule(restarted) == []


def test_a_restart_applies_an_event_missed_while_stopped(tmp_path):
    manager = new_manager(tmp_path)
    enable(manager, WORKDAYS, at(MON, "12:00"))
    manager.set_armed(False)
    restarted = new_manager(tmp_path)
    restarted.check_schedule(at(MON + 1, "09:00"))  # stopped over Tuesday's start
    assert armed(restarted)


def test_an_unreadable_memory_applies_the_schedule(tmp_path):
    manager = new_manager(tmp_path)
    enable(manager, WORKDAYS, at(MON, "12:00"))
    manager.set_armed(False)
    (tmp_path / "schedule_state.json").write_text("not json")
    restarted = new_manager(tmp_path)
    restarted.check_schedule(at(MON, "12:05"))
    assert armed(restarted)


def test_turning_the_schedule_on_applies_it_at_once(manager):
    enable(manager, WORKDAYS, at(MON, "09:00"))
    assert armed(manager)
    manager.settings.update({"schedule": {"enabled": False}})
    manager.check_schedule(at(MON, "09:30"))
    manager.set_armed(False)
    manager.settings.update({"schedule": {"enabled": True}})
    manager.check_schedule(at(MON, "10:00"))
    assert armed(manager)


def test_editing_another_day_keeps_a_manual_change(manager):
    weekend = [ScheduleRule(days=[6], start="10:00", end="12:00")]
    enable(manager, WORKDAYS + weekend, at(MON, "20:00"))
    manager.set_armed(True)                # armed by hand while the schedule says disarmed
    enable(manager, WORKDAYS + [ScheduleRule(days=[6], start="10:00", end="13:00")], at(MON, "20:01"))
    manager.check_schedule(at(MON, "20:02"))
    assert armed(manager)
    manager.check_schedule(at(MON + 1, "18:00"))
    assert not armed(manager), "the next end disarms as usual"


def test_editing_the_answer_for_now_applies_it_at_once(manager):
    enable(manager, WORKDAYS, at(MON, "20:00"))
    assert not armed(manager)
    enable(manager, WORKDAYS + NIGHTS, at(MON, "23:00"))   # tonight is now an armed period
    assert armed(manager)
    manager.set_armed(False)
    enable(manager, WORKDAYS + [ScheduleRule(days=EVERY_DAY, start="21:00", end="07:00")], at(MON, "23:05"))
    assert not armed(manager), "moving the start of the current period keeps the manual disarm"
    manager.set_armed(True)
    enable(manager, WORKDAYS, at(MON, "23:10"))            # tonight's period removed
    assert not armed(manager)


def test_back_to_back_periods_each_arm(manager):
    # "Every night 22:00-07:00" plus "Weekdays 07:00-18:00"
    enable(manager, NIGHTS + [ScheduleRule(days=[0, 1, 2, 3, 4], start="07:00", end="18:00")], at(MON, "06:00"))
    assert armed(manager)
    manager.set_armed(False)               # disarmed on waking up
    manager.check_schedule(at(MON, "06:59"))
    assert not armed(manager)
    manager.check_schedule(at(MON, "07:00"))
    assert armed(manager), "the workday period arms when it starts"
    manager.check_schedule(at(MON, "18:00"))
    assert not armed(manager)


def test_a_scheduled_disarm_waits_for_a_panic_to_end(manager):
    enable(manager, WORKDAYS, at(MON, "09:00"))
    manager.panic_active = True
    manager.check_schedule(at(MON, "18:00"))
    manager.check_schedule(at(MON, "18:01"))
    assert armed(manager)
    assert manager.schedule_status(at(MON, "18:01"))["waiting"] is True
    assert [d for t, d in manager.events.logged if t == "SYSTEM"] == ["Scheduled disarm waits until the alarm is reset or clears"]
    manager.panic_active = False           # reset
    manager.check_schedule(at(MON, "18:02"))
    assert not armed(manager)
    assert manager.schedule_status(at(MON, "18:02"))["waiting"] is False


def test_disarming_by_hand_ends_the_wait(manager):
    enable(manager, WORKDAYS, at(MON, "09:00"))
    manager.panic_active = True
    manager.check_schedule(at(MON, "18:00"))
    manager.set_armed(False)               # a panic outlasts a manual disarm
    manager.check_schedule(at(MON, "18:01"))
    assert manager.schedule_status(at(MON, "18:01"))["waiting"] is False
    manager.panic_active = False
    manager.set_armed(True)
    manager.check_schedule(at(MON, "18:02"))
    assert armed(manager), "the disarm was done"


def test_resetting_the_alarm_applies_a_waiting_disarm_at_once(manager):
    now = datetime.now().astimezone()
    start, end = (now - timedelta(hours=2)).strftime("%H:%M"), (now - timedelta(minutes=1)).strftime("%H:%M")
    enable(manager, [ScheduleRule(days=EVERY_DAY, start=start, end=end)], now - timedelta(hours=1))
    assert armed(manager)
    manager.panic_active = True
    manager.check_schedule()
    assert armed(manager)
    manager.reset_alarm()
    assert not armed(manager)


def test_a_scheduled_disarm_waits_for_an_intruder_alarm_to_clear(manager):
    enable(manager, WORKDAYS, at(MON, "09:00"))
    brain = add_unit(manager, threat_level=manager.settings.get().escalation.alert_at_level)
    manager.check_schedule(at(MON, "18:00"))
    assert armed(manager), "the schedule doesn't switch off an intruder alarm"
    brain.threat_level = 0                 # the person left
    manager.check_schedule(at(MON, "18:10"))
    assert not armed(manager)


def test_a_minor_incident_does_not_hold_up_a_scheduled_disarm(manager):
    enable(manager, WORKDAYS, at(MON, "09:00"))
    add_unit(manager, threat_level=1)
    manager.check_schedule(at(MON, "18:00"))
    assert not armed(manager)


def test_the_repeated_hour_does_not_rearm(new_york, manager):
    enable(manager, [ScheduleRule(days=EVERY_DAY, start="22:00", end="01:30")], at(31, "23:00"))
    assert armed(manager)
    for minutes in range(0, 4 * 60, 10):   # 00:00 EDT to 02:50 EST, through both 01:00-02:00 hours
        manager.check_schedule(utc(2026, 11, 1, 4, 0) + timedelta(minutes=minutes))
    assert not armed(manager)
    assert by_schedule(manager) == ["System armed by schedule",
                                    "System disarmed by schedule: detections will not raise alarms"]


def test_disabled_schedule_does_nothing(manager):
    manager.settings.update({"schedule": {"enabled": False, "rules": [WORKDAYS[0].model_dump()]}})
    manager.check_schedule(at(MON, "09:00"))
    assert not armed(manager)
    assert manager.schedule_status(at(MON, "09:00")) == {"enabled": False, "active": None, "next_change": None,
                                                         "waiting": False}


def test_status_reports_the_next_change_of_the_real_state(manager):
    enable(manager, WORKDAYS, at(MON, "09:00"))
    status = manager.schedule_status(at(MON, "09:00"))
    assert status["enabled"] and status["active"] is True and status["waiting"] is False
    assert datetime.fromisoformat(status["next_change"]) == at(MON, "18:00")
    manager.set_armed(False)
    status = manager.schedule_status(at(MON, "09:00"))
    assert status["active"] is True, "still the schedule's answer"
    assert datetime.fromisoformat(status["next_change"]) == at(MON + 1, "08:00"), "when it arms again"
