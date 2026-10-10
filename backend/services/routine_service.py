"""
Routine: when people are usually in view of each camera, so activity at unusual times stands out.

Each camera keeps a 7 x 24 grid (weekday x hour, this computer's local time) of minutes:

  observed   the camera was online and its pictures were checked for people
  people     at least one person was in view (insiders included)
  strangers  at least one unrecognised person was in view

The camera loop only sets flags while it detects; once a minute they become one minute of the grid.
Older weeks count less (half every HALF_LIFE_DAYS), so the grid follows a routine that changes.
The grid is kept in DATA_DIR/learning/routine/<camera id>.json.

A time is judged from its own hour, the hours either side, and the same hour on the other days of
the same kind (weekdays or the weekend), which count less. Until enough has been watched around
that time the answer is "still learning", never "unusual".
"""
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import DATA_DIR

ROUTINE_DIR = DATA_DIR / "learning" / "routine"
HALF_LIFE_DAYS = 28
DECAY_SECONDS = 3600  # the decay is applied at most this often
SAVE_SECONDS = 300
MIN_DAYS = 3  # different days watched before any time is judged
DAY_MINUTES = 60  # a day counts as watched once the camera watched this long on it
MIN_WINDOW_MINUTES = 180  # watched around a time before it is judged
UNUSUAL_SHARE = 0.025  # people in fewer minutes than this around a time: usually quiet
NEIGHBOUR_WEIGHT = 0.5  # the hours either side
OTHER_DAY_WEIGHT = 0.25  # the same hour on the other days of the same kind
KEEP_DAYS = 400  # watched dates remembered
SUMMARY_MINUTES = 60  # watched per hour of a kind of day before it is described

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_PLURALS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
KINDS = (("weekdays", (0, 1, 2, 3, 4)), ("weekends", (5, 6)))
GRIDS = ("observed", "people", "strangers")


def _grid() -> list[list[float]]:
    return [[0.0] * 24 for _ in range(7)]


def cell(when: float) -> tuple[int, int]:
    """(weekday, hour) of a moment on this computer's clock, which follows daylight saving time."""
    local = datetime.fromtimestamp(when)
    return local.weekday(), local.hour


def hour_words(hour: int) -> str:
    if hour == 0:
        return "midnight"
    if hour == 12:
        return "noon"
    return f"{hour} am" if hour < 12 else f"{hour - 12} pm"


@dataclass
class Judgement:
    day: int
    hour: int
    expected: float | None  # share of the minutes watched around this time that had people
    watched_minutes: float  # minutes watched around this time
    days_watched: int

    @property
    def confident(self) -> bool:
        return self.days_watched >= MIN_DAYS and self.watched_minutes >= MIN_WINDOW_MINUTES

    @property
    def unusual(self) -> bool:
        """Nobody is usually there at this time, as far as Guardian has watched."""
        return self.confident and self.expected is not None and self.expected < UNUSUAL_SHARE

    def quiet_note(self, camera: str) -> str:
        return f"{camera} is usually quiet on {DAY_PLURALS[self.day]} around {hour_words(self.hour)}"


class Routine:
    """One camera's grid. The camera loop feeds it; the brain and the dashboard read it."""

    def __init__(self):
        self._lock = threading.Lock()
        self.observed, self.people, self.strangers = _grid(), _grid(), _grid()
        self.days: dict[str, float] = {}  # local date -> minutes watched that day
        self.decayed_at: float | None = None
        self.dirty = False
        self.saved_at = 0.0
        self._minute: int | None = None
        self._seen = self._people = self._strangers = False  # flags for the current minute

    # ---- learning --------------------------------------------------------------
    def saw(self, now: float, people: bool, strangers: bool) -> None:
        """The camera checked a picture for people. Only sets flags; cheap enough for the camera loop."""
        with self._lock:
            self._roll(now)
            self._seen = True
            self._people = self._people or people or strangers
            self._strangers = self._strangers or strangers

    def tick(self, now: float) -> None:
        """Turns the last minute's flags into a minute of the grid once that minute is over."""
        with self._lock:
            self._roll(now)

    def _roll(self, now: float) -> None:
        minute = int(now // 60)
        if minute == self._minute:
            return
        if self._minute is not None and self._seen:
            self._add(self._minute * 60, self._people, self._strangers)
        self._minute = minute
        self._seen = self._people = self._strangers = False

    def _add(self, when: float, people: bool, strangers: bool) -> None:
        self._decay(when)
        day, hour = cell(when)
        self.observed[day][hour] += 1
        self.people[day][hour] += people
        self.strangers[day][hour] += strangers
        date = datetime.fromtimestamp(when).date().isoformat()
        self.days[date] = self.days.get(date, 0) + 1
        if len(self.days) > KEEP_DAYS:
            self.days.pop(min(self.days))
        self.dirty = True

    def _decay(self, now: float) -> None:
        """Older minutes count less: half as much every HALF_LIFE_DAYS. Applied lazily."""
        if self.decayed_at is None or now < self.decayed_at:  # first minute, or the clock was put back
            self.decayed_at = now
            return
        elapsed = now - self.decayed_at
        if elapsed < DECAY_SECONDS:
            return
        factor = 0.5 ** (elapsed / (HALF_LIFE_DAYS * 86400))
        for grid in (self.observed, self.people, self.strangers):
            for row in grid:
                row[:] = [v * factor for v in row]
        self.decayed_at = now

    def clear(self) -> None:
        with self._lock:
            self.observed, self.people, self.strangers = _grid(), _grid(), _grid()
            self.days, self.decayed_at, self.dirty = {}, None, False
            self._minute, self._seen, self._people, self._strangers = None, False, False, False

    # ---- judging ---------------------------------------------------------------
    def days_watched(self) -> int:
        return sum(1 for minutes in self.days.values() if minutes >= DAY_MINUTES)

    @staticmethod
    def _window(day: int, hour: int) -> list[tuple[int, int, float]]:
        """The cells that judge (day, hour), with their weights. The hours either side may be on the
        day before or after; the other days are the same kind of day, at the same hour."""
        i = day * 24 + hour
        cells = [(i, 1.0), ((i - 1) % 168, NEIGHBOUR_WEIGHT), ((i + 1) % 168, NEIGHBOUR_WEIGHT)]
        same_kind = next(days for _, days in KINDS if day in days)
        cells += [(other * 24 + hour, OTHER_DAY_WEIGHT) for other in same_kind if other != day]
        return [(j // 24, j % 24, weight) for j, weight in cells]

    def _judge(self, day: int, hour: int, days_watched: int) -> Judgement:
        people = observed = watched = 0.0
        for d, h, weight in self._window(day, hour):
            people += weight * self.people[d][h]
            observed += weight * self.observed[d][h]
            watched += self.observed[d][h]
        return Judgement(day, hour, people / observed if observed > 0 else None, watched, days_watched)

    def judge(self, now: float) -> Judgement:
        """What Guardian expects at `now`: how often people are around at this time."""
        with self._lock:
            self._decay(now)
            return self._judge(*cell(now), self.days_watched())

    def status(self, now: float) -> dict:
        judgement = self.judge(now)
        return {"learning": not judgement.confident, "days_watched": judgement.days_watched,
                "days_needed": MIN_DAYS}

    def report(self, now: float) -> dict:
        """The grid for the dashboard, with what it says in plain words and what is expected now."""
        with self._lock:
            self._decay(now)
            days = self.days_watched()
            judged = [[self._judge(d, h, days) for h in range(24)] for d in range(7)]
            observed = [[round(v, 1) for v in row] for row in self.observed]
            people = [[round(v, 1) for v in row] for row in self.people]
            strangers = [[round(v, 1) for v in row] for row in self.strangers]
            share = [[_share(self.people[d][h], self.observed[d][h]) for h in range(24)] for d in range(7)]
            summary = self._summary() if days >= MIN_DAYS else []
        now_day, now_hour = cell(now)
        current = judged[now_day][now_hour]
        return {
            "days_watched": days,
            "days_needed": MIN_DAYS,
            "observed_minutes": observed,
            "people_minutes": people,
            "stranger_minutes": strangers,
            "share": share,
            "expected": [[_round(j.expected) for j in row] for row in judged],
            "confident": [[j.confident for j in row] for row in judged],
            "summary": summary,
            "now": {"day": now_day, "hour": now_hour, "expected": _round(current.expected),
                    "confident": current.confident, "unusual": current.unusual},
            "unusual_below": UNUSUAL_SHARE,
        }

    def _summary(self) -> list[str]:
        """Typical busy and quiet times, per kind of day, e.g. "Busiest: weekdays 17:00–19:00"."""
        lines = []
        for label, days in KINDS:
            shares: list[float | None] = []
            for hour in range(24):
                watched = sum(self.observed[d][hour] for d in days)
                seen = sum(self.people[d][hour] for d in days)
                shares.append(seen / watched if watched >= SUMMARY_MINUTES else None)
            busy = _busiest(shares)
            if busy:
                lines.append(f"Busiest: {label} {_span(*busy)}")
            quiet = _quietest(shares)
            if quiet:
                lines.append(f"Usually quiet: {label} {'all day' if quiet[1] - quiet[0] >= 24 else _span(*quiet)}")
        return lines

    # ---- storage ---------------------------------------------------------------
    def dump(self) -> dict:
        with self._lock:
            return {"version": 1, "decayed_at": self.decayed_at, "days": dict(self.days),
                    **{name: [[round(v, 3) for v in row] for row in getattr(self, name)] for name in GRIDS}}

    @classmethod
    def from_dict(cls, data: dict) -> "Routine":
        routine = cls()
        for name in GRIDS:
            grid = data[name]
            if len(grid) != 7 or any(len(row) != 24 for row in grid):
                raise ValueError(f"{name} is not a 7 x 24 grid")
            setattr(routine, name, [[max(0.0, float(v)) for v in row] for row in grid])
        routine.days = {str(k): float(v) for k, v in data.get("days", {}).items()}
        decayed_at = data.get("decayed_at")
        routine.decayed_at = float(decayed_at) if decayed_at is not None else None
        return routine


def _share(people: float, observed: float) -> float | None:
    return round(min(1.0, people / observed), 4) if observed > 0 else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _span(start: int, end: int) -> str:
    return f"{start % 24:02d}:00–{end % 24:02d}:00"


def _busiest(shares: list[float | None], longest: int = 6) -> tuple[int, int] | None:
    """The hours around the busiest one that are at least half as busy, or None if nobody is usually seen."""
    known = [(s, h) for h, s in enumerate(shares) if s is not None]
    if not known:
        return None
    peak, hour = max(known)
    if peak < 0.05:
        return None
    start, end = hour, hour + 1

    def busy(h: int) -> bool:
        return 0 <= h < 24 and shares[h] is not None and shares[h] >= peak / 2

    while end - start < longest:
        before, after = busy(start - 1), busy(end)
        if before and (not after or shares[start - 1] >= shares[end]):
            start -= 1
        elif after:
            end += 1
        else:
            break
    return start, end


def _quietest(shares: list[float | None], shortest: int = 2) -> tuple[int, int] | None:
    """The longest run of usually quiet hours, which may run past midnight; None if under `shortest`."""
    quiet = [s is not None and s < UNUSUAL_SHARE for s in shares]
    if all(quiet):
        return 0, 24
    best, run_start = (0, 0), None
    for i in range(48):  # twice round the clock, so a run past midnight is found whole
        if quiet[i % 24]:
            run_start = i if run_start is None else run_start
            if i - run_start + 1 > best[1] - best[0]:
                best = (run_start, i + 1)
        else:
            run_start = None
    return best if best[1] - best[0] >= shortest else None


class RoutineService:
    """Every camera's routine, loaded when first needed and saved every few minutes."""

    def __init__(self, folder: Path = ROUTINE_DIR):
        self.folder = folder
        self._lock = threading.Lock()  # also held while writing, so a reset can't be undone by a save
        self._routines: dict[str, Routine] = {}

    def _path(self, camera_id: str) -> Path:
        return self.folder / f"{camera_id}.json"

    def get(self, camera_id: str) -> Routine:
        with self._lock:
            routine = self._routines.get(camera_id)
            if routine is None:
                routine = self._routines[camera_id] = self._load(camera_id)
            return routine

    def _load(self, camera_id: str) -> Routine:
        path = self._path(camera_id)
        try:
            return Routine.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            print(f"[routine] Starting afresh: {path.name} is unreadable ({exc})")
        return Routine()

    def flush(self, now: float, force: bool = False) -> None:
        """Saves routines that changed, at most every SAVE_SECONDS each unless forced."""
        with self._lock:
            for camera_id, routine in self._routines.items():
                if routine.dirty and (force or now - routine.saved_at >= SAVE_SECONDS):
                    self._save(camera_id, routine, now)

    def save(self, camera_id: str, now: float) -> None:
        with self._lock:
            routine = self._routines.get(camera_id)
            if routine is not None and routine.dirty:
                self._save(camera_id, routine, now)

    def _save(self, camera_id: str, routine: Routine, now: float) -> None:
        routine.dirty, routine.saved_at = False, now
        path = self._path(camera_id)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(routine.dump()), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            routine.dirty = True
            print(f"[routine] Could not save {path.name}: {exc}")

    def reset(self, camera_id: str) -> None:
        """Forgets what was learned; the camera starts learning again from now."""
        with self._lock:
            routine = self._routines.get(camera_id)
            if routine is not None:
                routine.clear()
            self._path(camera_id).unlink(missing_ok=True)

    def forget(self, camera_id: str) -> None:
        """The camera was deleted."""
        with self._lock:
            routine = self._routines.pop(camera_id, None)
            if routine is not None:
                routine.clear()
            self._path(camera_id).unlink(missing_ok=True)


routine_service = RoutineService()
