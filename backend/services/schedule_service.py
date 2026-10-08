"""
Arming schedule: Guardian arms itself during the configured periods and disarms outside them.

Times are the server's local time. A period whose end is earlier than its start runs past
midnight and belongs to the day it starts on ("Fri 22:00-07:00" ends on Saturday morning).
"""
from datetime import datetime, timedelta

from services.settings_service import ScheduleRule


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def armed_at(rules: list[ScheduleRule], when: datetime) -> bool:
    minute, day = when.hour * 60 + when.minute, when.weekday()
    for rule in rules:
        start, end = _minutes(rule.start), _minutes(rule.end)
        if start < end:
            if day in rule.days and start <= minute < end:
                return True
        elif (day in rule.days and minute >= start) or ((day - 1) % 7 in rule.days and minute < end):
            return True
    return False


def next_change(rules: list[ScheduleRule], when: datetime) -> datetime | None:
    """The next moment armed_at() gives a different answer, or None if it never does."""
    current = armed_at(rules, when)
    midnight = when.replace(hour=0, minute=0, second=0, microsecond=0)
    # The answer can only change where a period starts or ends
    marks = sorted({midnight + timedelta(days=day, minutes=_minutes(t))
                    for day in range(9) for rule in rules for t in (rule.start, rule.end)})
    return next((m for m in marks if m > when and armed_at(rules, m) != current), None)
