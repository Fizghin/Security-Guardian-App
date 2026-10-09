"""
Arming schedule: Guardian arms itself when a period starts and disarms when the armed time ends.

Times are this computer's local time. A period whose end is earlier than its start runs past
midnight and belongs to the day it starts on ("Fri 22:00-07:00" ends on Saturday morning).

The schedule is a list of events: "arm" at every period start, and "disarm" where the periods
(joined where they touch or overlap) end. Events are moments in absolute time, so a time the clocks
skip when they go forward happens when they jump, and a time they show twice when they go back
happens only the first time.
"""
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from services.settings_service import ScheduleRule

Event = tuple[datetime, bool]  # (when, whether it arms)

SEARCH_DAYS = 8  # every rule repeats weekly, so the last and the next event are always this close


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _moment(wall: datetime) -> datetime:
    """When this computer's clock shows `wall`. A time the clocks skip gives the moment they jump;
    a time they show twice gives the first one."""
    first = wall.astimezone()
    if first.replace(tzinfo=None) == wall:
        return first
    # Skipped: `first` is after the jump, and the same time with fold=1 is before it. Find the jump.
    before, after = int(wall.replace(fold=1).timestamp()), int(first.timestamp())
    while after - before > 1:
        middle = (before + after) // 2
        if datetime.fromtimestamp(middle, timezone.utc).astimezone().utcoffset() == first.utcoffset():
            after = middle
        else:
            before = middle
    return datetime.fromtimestamp(after, timezone.utc).astimezone()


def _periods(rules: list[ScheduleRule], today: date) -> list[tuple[datetime, datetime]]:
    # Two days beyond the search window, so joined periods that cross its edge end in the right place
    periods = []
    for offset in range(-SEARCH_DAYS - 2, SEARCH_DAYS + 3):
        day = datetime.combine(today + timedelta(days=offset), time())
        for rule in rules:
            if day.weekday() not in rule.days:
                continue
            start, end = _minutes(rule.start), _minutes(rule.end)
            begins = _moment(day + timedelta(minutes=start))
            ends = _moment(day + timedelta(days=1 if end < start else 0, minutes=end))
            if begins < ends:  # a period inside the hour the clocks skip doesn't happen that day
                periods.append((begins, ends))
    return sorted(periods)


def events(rules: list[ScheduleRule], now: datetime) -> list[Event]:
    """The schedule's events within SEARCH_DAYS of `now`, oldest first."""
    now = now.astimezone()
    periods = _periods(rules, now.date())
    blocks: list[list[datetime]] = []
    for start, end in periods:
        if blocks and start <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], end)
        else:
            blocks.append([start, end])
    found = {start: True for start, _ in periods} | {end: False for _, end in blocks}
    window = timedelta(days=SEARCH_DAYS)
    return sorted((when, arms) for when, arms in found.items() if abs(when - now) <= window)


def last_event(rules: list[ScheduleRule], now: datetime) -> Event | None:
    """The latest event at or before `now`, or None if there are no periods."""
    now = now.astimezone()
    return next((e for e in reversed(events(rules, now)) if e[0] <= now), None)


def armed_at(rules: list[ScheduleRule], now: datetime) -> bool:
    """Whether the schedule wants Guardian armed at `now`."""
    event = last_event(rules, now)
    return bool(event and event[1])


def next_change(rules: list[ScheduleRule], now: datetime, armed: bool) -> datetime | None:
    """The next event that changes `armed` (an "arm" while armed changes nothing), or None."""
    now = now.astimezone()
    return next((when for when, arms in events(rules, now) if when > now and arms != armed), None)


def time_zone(now: datetime | None = None) -> dict:
    """This computer's time zone, which the schedule's times are in."""
    now = (now or datetime.now()).astimezone()
    offset = now.strftime("%z")
    return {"name": now.tzname(), "utc_offset": f"{offset[:3]}:{offset[3:5]}"}


class AppliedEvent:
    """The schedule event Guardian last acted on, kept on disk so a restart doesn't act on it again."""

    def __init__(self, path: Path):
        self.path = path
        self.event: Event | None = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.event = (datetime.fromisoformat(data["when"]).astimezone(), bool(data["armed"]))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"[schedule] Ignoring unreadable {path.name}: {exc}")

    def remember(self, event: Event | None) -> None:
        if event == self.event:
            return
        self.event = event
        try:
            if event is None:
                self.path.unlink(missing_ok=True)
                return
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"when": event[0].isoformat(), "armed": event[1]}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[schedule] Could not save {self.path.name}: {exc}")
