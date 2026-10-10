"""
Activity insights from the event log: when and where people show up, and what is unusual.

Everything is computed from events already stored, in this computer's local time
(the same clock the schedule uses), so it works on existing history straight away.

- A weekday x hour grid of incidents (an unrecognised person appearing while armed).
- Per-camera activity, and a day-by-day series.
- A night-activity check: incidents between 22:00 and 06:00 in the last 24 hours
  compared with the average night of the period before.
- A 0-100 threat score for the last 24 hours, with the reasons behind it.
- Plain-language findings ("Front door sees 64% of all activity").
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from models.database import SecurityEvent, SessionLocal

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
NIGHT_START, NIGHT_END = 22, 6


def _local(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc).astimezone()


def _is_night(dt: datetime) -> bool:
    return dt.hour >= NIGHT_START or dt.hour < NIGHT_END


def _is_test(description: str | None) -> bool:
    return "(test)" in (description or "")


def _night_of(dt: datetime):
    """The date the most recent night started on: 02:00 on the 5th belongs to the night of the 4th."""
    return (dt - timedelta(hours=NIGHT_START)).date()


def _hour_range(hour: int) -> str:
    return f"{hour:02d}:00–{(hour + 1) % 24:02d}:00"


def insights(days: int = 30, now: datetime | None = None, session_factory=SessionLocal) -> dict:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    since = now_utc - timedelta(days=days)
    db = session_factory()
    try:
        rows = db.query(SecurityEvent.timestamp, SecurityEvent.event_type, SecurityEvent.severity,
                        SecurityEvent.camera, SecurityEvent.description) \
            .filter(SecurityEvent.timestamp >= since).all()
    finally:
        db.close()

    grid = [[0] * 24 for _ in range(7)]
    per_camera: dict[str, Counter] = defaultdict(Counter)
    per_day: dict[str, Counter] = defaultdict(Counter)
    hours = Counter()
    totals = Counter()
    recent = Counter()  # last 24 hours
    nights: Counter = Counter()  # incidents per night (keyed by the date the night started)
    day_cutoff = now_utc - timedelta(hours=24)

    for ts, etype, severity, camera, description in rows:
        if _is_test(description):
            continue
        local = _local(ts)
        cam = camera or "System"
        day = local.date().isoformat()
        if etype == "DETECTION":
            grid[local.weekday()][local.hour] += 1
            hours[local.hour] += 1
            per_camera[cam]["incidents"] += 1
            per_day[day]["incidents"] += 1
            totals["incidents"] += 1
            if _is_night(local):
                night_of = _night_of(local)
                nights[night_of] += 1
        elif etype == "ALERT":
            per_camera[cam]["alerts"] += 1
            per_day[day]["alerts"] += 1
            totals["alerts"] += 1
        elif etype == "SIREN":
            per_camera[cam]["sirens"] += 1
            totals["sirens"] += 1
        elif etype == "SOUND":
            per_camera[cam]["sounds"] += 1
            per_day[day]["sounds"] += 1
            totals["sounds"] += 1
        elif etype == "INSIDER":
            per_camera[cam]["insiders"] += 1
            totals["insiders"] += 1
        elif etype == "VISITOR":
            totals["returning"] += 1
        elif etype == "UNATTENDED":
            per_camera[cam]["unattended"] += 1
            totals["unattended"] += 1
        elif etype == "CAMERA_OFFLINE":
            per_camera[cam]["offline"] += 1
            totals["offline"] += 1
        if etype == "DETECTION" and "seen before" in (description or ""):
            totals["returning"] += 1
        if ts >= day_cutoff:
            recent[etype] += 1
            if etype == "DETECTION" and _is_night(local):
                recent["night_incidents"] += 1

    # Day-by-day series in local dates, including quiet days
    series = []
    start_day = _local(since).date()
    end_day = _local(now_utc).date()
    d = start_day
    while d <= end_day:
        c = per_day.get(d.isoformat(), Counter())
        series.append({"date": d.isoformat(), "incidents": c["incidents"], "alerts": c["alerts"], "sounds": c["sounds"]})
        d += timedelta(days=1)

    # Night baseline: average incidents per night over the period, without the last night
    last_night = _night_of(_local(now_utc))
    previous_nights = max(1, days - 1)
    baseline = sum(n for night, n in nights.items() if night != last_night) / previous_nights
    tonight = recent["night_incidents"]
    night_ratio = (tonight / baseline) if baseline > 0 else (float(tonight) if tonight else 0.0)

    score, reasons = _threat_score(recent, tonight, baseline)

    cameras = sorted(({"camera": cam, **{k: c.get(k, 0) for k in ("incidents", "alerts", "sirens", "sounds",
                                                                   "insiders", "offline", "unattended")}}
                      for cam, c in per_camera.items() if cam != "System"),
                     key=lambda r: (-r["incidents"], -r["alerts"], r["camera"]))

    return {
        "days": days,
        "generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "time_zone": _local(now_utc).tzname(),
        "weekdays": WEEKDAYS,
        "grid": grid,
        "grid_max": max((v for row in grid for v in row), default=0),
        "hours": [hours.get(h, 0) for h in range(24)],
        "series": series,
        "cameras": cameras,
        "totals": {k: totals.get(k, 0) for k in ("incidents", "alerts", "sirens", "sounds", "insiders", "returning",
                                                 "offline", "unattended")},
        "last_24h": {"incidents": recent["DETECTION"], "alerts": recent["ALERT"], "sirens": recent["SIREN"],
                     "sounds": recent["SOUND"], "unattended": recent["UNATTENDED"], "night_incidents": tonight},
        "night": {"start": NIGHT_START, "end": NIGHT_END, "last_night": tonight, "baseline": round(baseline, 2),
                  "ratio": round(night_ratio, 2)},
        "threat": {"score": score, "level": _score_level(score), "reasons": reasons},
        "findings": _findings(grid, hours, cameras, totals, tonight, baseline, days),
    }


def _threat_score(recent: Counter, tonight: int, baseline: float) -> tuple[int, list[str]]:
    """How tense the last 24 hours were, 0-100, with each contribution explained."""
    parts: list[tuple[int, str]] = []
    if recent["SIREN"]:
        parts.append((35, f"The siren sounded {recent['SIREN']} time{'s' if recent['SIREN'] > 1 else ''}"))
    if recent["ALERT"]:
        parts.append((min(30, 12 * recent["ALERT"]), f"{recent['ALERT']} owner alert{'s' if recent['ALERT'] > 1 else ''}"))
    if recent["PANIC"]:
        parts.append((25, "The panic button was used"))
    if tonight:
        extra = tonight - baseline
        if extra > 0.5:
            parts.append((min(25, int(8 * extra)), f"{tonight} night-time incident{'s' if tonight > 1 else ''} "
                                                   f"(usually {baseline:.1f})"))
    if recent["DETECTION"] and not tonight:
        parts.append((min(15, 3 * recent["DETECTION"]), f"{recent['DETECTION']} incident{'s' if recent['DETECTION'] > 1 else ''} in daytime"))
    if recent["SOUND"]:
        parts.append((min(10, 2 * recent["SOUND"]), f"{recent['SOUND']} loud sound{'s' if recent['SOUND'] > 1 else ''}"))
    if recent["UNATTENDED"]:
        n = recent["UNATTENDED"]
        parts.append((min(20, 10 * n), f"{n} unattended object{'s' if n > 1 else ''} left behind"))
    if recent["CAMERA_OFFLINE"]:
        parts.append((min(10, 5 * recent["CAMERA_OFFLINE"]), "A camera went offline"))
    score = min(100, sum(p for p, _ in parts))
    return score, [r for _, r in sorted(parts, reverse=True)]


def _score_level(score: int) -> str:
    return "calm" if score < 15 else "low" if score < 35 else "elevated" if score < 65 else "high"


def _findings(grid, hours: Counter, cameras: list[dict], totals: Counter, tonight: int, baseline: float,
              days: int) -> list[dict]:
    out: list[dict] = []
    incidents = totals["incidents"]
    if not incidents:
        out.append({"kind": "info", "text": f"No incidents in the last {days} days."})
    else:
        day, hour = max(((d, h) for d in range(7) for h in range(24)), key=lambda dh: grid[dh[0]][dh[1]])
        out.append({"kind": "info", "text": f"Busiest time: {WEEKDAYS[day]} {_hour_range(hour)} "
                                            f"({grid[day][hour]} incident{'s' if grid[day][hour] > 1 else ''})."})
        night = sum(hours.get(h, 0) for h in range(24) if h >= NIGHT_START or h < NIGHT_END)
        share = night / incidents
        if share >= 0.4 and night >= 3:
            out.append({"kind": "warning", "text": f"{share:.0%} of incidents happen at night "
                                                   f"({NIGHT_START:02d}:00–{NIGHT_END:02d}:00). Make sure the "
                                                   "schedule arms Guardian before then."})
        if cameras and len(cameras) > 1 and cameras[0]["incidents"]:
            top = cameras[0]
            out.append({"kind": "info", "text": f"{top['camera']} sees {top['incidents'] / incidents:.0%} "
                                                "of all incidents."})
    if tonight and baseline and tonight >= 2 * baseline + 1:
        out.append({"kind": "alert", "text": f"Last night was unusually busy: {tonight} incidents against "
                                             f"{baseline:.1f} on an average night."})
    if totals["returning"]:
        out.append({"kind": "warning", "text": f"Remembered visitors came back {totals['returning']} "
                                               f"time{'s' if totals['returning'] > 1 else ''}. See Visitors for who."})
    quiet = [c["camera"] for c in cameras if c["offline"] >= 3]
    if quiet:
        out.append({"kind": "warning", "text": f"{', '.join(quiet)} dropped offline repeatedly. Check power and Wi-Fi."})
    if totals["alerts"] and incidents:
        out.append({"kind": "info", "text": f"{totals['alerts'] / incidents:.0%} of incidents reached the alert level."})
    return out
