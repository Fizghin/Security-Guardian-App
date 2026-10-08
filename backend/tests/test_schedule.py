from datetime import datetime

import pytest

from services.camera_service import CameraManager
from services.schedule_service import armed_at, next_change
from services.settings_service import ScheduleRule, SettingsError, SettingsService

# 2026-10-05 is a Monday
MON, SAT, SUN = 5, 10, 11


def at(day: int, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, h, m)


NIGHTS = [ScheduleRule(days=[0, 1, 2, 3, 4, 5, 6], start="22:00", end="07:00")]
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
    assert next_change(WORKDAYS, at(MON, "09:00")) == at(MON, "18:00")
    assert next_change(WORKDAYS, at(SAT, "09:00")) == datetime(2026, 10, 12, 8, 0)  # Monday morning
    assert next_change(NIGHTS, at(MON, "12:00")) == at(MON, "22:00")
    assert next_change(NIGHTS, at(MON, "23:00")) == datetime(2026, 10, 6, 7, 0)
    # Back-to-back periods don't count as a change
    joined = WORKDAYS + [ScheduleRule(days=[0, 1, 2, 3, 4], start="18:00", end="20:00")]
    assert next_change(joined, at(MON, "09:00")) == at(MON, "20:00")
    assert next_change([], at(MON, "09:00")) is None


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


class Events:
    def __init__(self):
        self.logged = []

    def log(self, event_type, description, *args, **kwargs):
        self.logged.append((event_type, description))


@pytest.fixture
def manager(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    svc.update({"armed": False})
    m = CameraManager(settings=svc, events=Events())
    return m


def enable(manager, rules):
    manager.settings.update({"schedule": {"enabled": True, "rules": [r.model_dump() for r in rules]}})


def armed(manager) -> bool:
    return manager.settings.get().armed


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


def test_editing_the_schedule_applies_it_at_once(manager):
    enable(manager, WORKDAYS)
    manager.check_schedule(at(MON, "09:00"))
    manager.set_armed(False)
    enable(manager, WORKDAYS + NIGHTS)
    manager.check_schedule(at(MON, "09:00"))
    assert armed(manager)


def test_schedule_does_not_cancel_a_panic(manager):
    enable(manager, WORKDAYS)
    manager.check_schedule(at(MON, "09:00"))
    manager.panic_active = True
    manager.check_schedule(at(MON, "18:00"))
    assert armed(manager)


def test_disabled_schedule_does_nothing(manager):
    manager.settings.update({"schedule": {"enabled": False, "rules": [WORKDAYS[0].model_dump()]}})
    manager.check_schedule(at(MON, "09:00"))
    assert not armed(manager)
    assert manager.schedule_status(at(MON, "09:00")) == {"enabled": False, "active": None, "next_change": None}


def test_status_reports_the_next_change(manager):
    enable(manager, WORKDAYS)
    status = manager.schedule_status(at(MON, "09:00"))
    assert status["enabled"] and status["active"] is True
    assert datetime.fromisoformat(status["next_change"]).replace(tzinfo=None) == at(MON, "18:00")
