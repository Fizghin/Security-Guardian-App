"""
Daily briefing: a short, calm summary of the last 24 hours, written from the event log.

The facts come from the log: incidents with unrecognised people and how far each one escalated,
alerts sent, insiders recognised, cameras that went offline, clips saved and arming changes. The
local language model turns them into 3-5 sentences. Its reply is checked so it states nothing the
facts don't (numbers, names, things like police or damage), and is not a refusal; it gets one retry
with the reason, then a briefing written from a template is used. A day with nothing to report gets
a short line without asking the model.

The latest briefing is kept in storage/briefing.json. One is written every day at the time set in
Settings → Briefing (and sent through the alert channels if asked), from the manager's background
loop; the day it last ran is kept in the same file, so a restart doesn't run it twice.
"""
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx

from config import DATA_DIR
from models.database import SecurityEvent, SessionLocal, to_iso
from services.ai_service import AIError, ai_service
from services.event_service import event_service
from services.notification_service import notification_service
from services.settings_service import settings_service

BRIEFING_FILE = DATA_DIR / "briefing.json"
PERIOD_HOURS = 24
LIST_LIMIT = 20  # entries kept per list in the facts; the counts cover everything
MODEL_TIMEOUT = 180  # a background job, so a slow CPU gets plenty of time
LEVEL_NAMES = {1: "Person detected", 2: "Loitering", 3: "Intruder", 4: "Alarm"}
WATCHED = ("DETECTION", "ESCALATION", "CLEARED", "ALERT", "INSIDER", "CAMERA_OFFLINE", "CAMERA_ONLINE",
           "CLIP_SAVED", "ARMED", "DISARMED", "PANIC", "SYSTEM")

_PEOPLE = re.compile(r"^(\d+) unrecognised people")
_LEVEL = re.compile(r"^Threat level (\d)")
_CLEARED = re.compile(r"lasted (\d+)s, peak level (\d)")
_BACK = re.compile(r"back after (\d+)s")


# ---- facts --------------------------------------------------------------------------
def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()


def clock(iso: str) -> str:
    """"07:05" in this computer's time zone."""
    return _local(iso).strftime("%H:%M")


def collect_facts(now: datetime, armed_now: bool) -> dict:
    """What the event log says about the PERIOD_HOURS before `now` (an aware time)."""
    end = now.astimezone(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(hours=PERIOD_HOURS)
    db = SessionLocal()
    try:
        rows = (db.query(SecurityEvent.timestamp, SecurityEvent.event_type, SecurityEvent.description,
                         SecurityEvent.camera)
                .filter(SecurityEvent.timestamp >= start, SecurityEvent.timestamp <= end,
                        SecurityEvent.event_type.in_(WATCHED))
                .order_by(SecurityEvent.timestamp, SecurityEvent.id).all())
    finally:
        db.close()

    incidents, alerts, offline, arming, panics, starts = [], [], [], [], [], []
    stopped = None
    insiders: dict[str, dict] = {}
    open_incidents: dict[str, tuple[dict, bool]] = {}  # camera -> (incident, is a test)
    open_offline: dict[str, dict] = {}
    unknown = recordings = tests = 0
    for ts, etype, desc, camera in rows:
        when, key, desc = to_iso(ts), camera or "", desc or ""
        if etype == "DETECTION":
            test = "(test)" in desc
            incident = {"camera": camera, "time": when, "peak_level": 1}
            open_incidents[key] = (incident, test)
            if test:
                tests += 1
            else:
                incidents.append(incident)
                m = _PEOPLE.match(desc)
                unknown += int(m.group(1)) if m else 1
        elif etype == "ESCALATION":
            m = _LEVEL.match(desc)
            if m and key in open_incidents:
                incident = open_incidents[key][0]
                incident["peak_level"] = max(incident["peak_level"], int(m.group(1)))
        elif etype == "CLEARED":
            m = _CLEARED.search(desc)
            current = open_incidents.pop(key, None)
            if current and m:
                current[0]["peak_level"] = max(current[0]["peak_level"], int(m.group(2)))
            elif current is None and m:
                # Started before the period, or a panic (which logs no detection)
                started = ts - timedelta(seconds=int(m.group(1)))
                incidents.append({"camera": camera, "time": to_iso(started), "peak_level": int(m.group(2))})
        elif etype == "ALERT":
            alerts.append({"camera": camera, "time": when})
        elif etype == "INSIDER" and desc.startswith("Recognised "):
            name = desc.removeprefix("Recognised ").strip()
            seen = insiders.setdefault(name, {"name": name, "first": when, "last": when, "cameras": []})
            seen["last"] = when
            if camera and camera not in seen["cameras"]:
                seen["cameras"].append(camera)
        elif etype == "CAMERA_OFFLINE":
            entry = {"camera": camera, "time": when, "back_after_seconds": None}
            offline.append(entry)
            open_offline[key] = entry
        elif etype == "CAMERA_ONLINE":
            m = _BACK.search(desc)
            entry = open_offline.pop(key, None)
            if entry and m:
                entry["back_after_seconds"] = int(m.group(1))
        elif etype == "CLIP_SAVED":
            recordings += 1
        elif etype in ("ARMED", "DISARMED"):
            arming.append({"time": when, "armed": etype == "ARMED", "by": "schedule" if "by schedule" in desc else "hand"})
        elif etype == "PANIC":
            panics.append(when)
        elif etype == "SYSTEM" and desc.startswith("Guardian stopped"):
            stopped = when
        elif etype == "SYSTEM" and desc.startswith("Guardian started"):
            # Without a logged stop (the period began, or it crashed) it is unknown since when it was off
            starts.append({"time": when, "stopped_at": stopped})
            stopped = None

    incidents.sort(key=lambda i: i["time"])
    return {
        "counts": {"incidents": len(incidents), "unknown_people": unknown, "alerts": len(alerts),
                   "insiders": len(insiders), "offline": len(offline), "recordings": recordings,
                   "arming_changes": len(arming), "panics": len(panics), "tests": tests},
        "incidents": incidents[:LIST_LIMIT],
        "alerts": alerts[:LIST_LIMIT],
        "insiders": list(insiders.values())[:LIST_LIMIT],
        "offline": offline[:LIST_LIMIT],
        "arming": arming[-LIST_LIMIT:],
        "panics": panics[:LIST_LIMIT],
        "starts": starts[:LIST_LIMIT],
        # set_armed only logs real changes, so the first one shows the state before it
        "armed_at_start": (not arming[0]["armed"]) if arming else armed_now,
        "armed_now": armed_now,
    }


def is_quiet(facts: dict) -> bool:
    return not any(facts["counts"].values())


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else plural or word + 's'}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _duration(seconds: int) -> str:
    if seconds < 90:
        return _plural(seconds, "second")
    if seconds < 5400:
        return _plural(round(seconds / 60), "minute")
    return _plural(round(seconds / 3600), "hour")


def _level(n: int) -> str:
    return f"level {n} of 4 ({LEVEL_NAMES.get(n, '?')})"


def _seen(insider: dict) -> str:
    first, last = clock(insider["first"]), clock(insider["last"])
    return f"at {first}" if first == last else f"from {first} to {last}"


def facts_text(facts: dict, now: datetime) -> str:
    """The facts as the model sees them. Every number and name it may use is in here."""
    c = facts["counts"]
    start = now - timedelta(hours=PERIOD_HOURS)
    lines = [f"Period: the last {PERIOD_HOURS} hours, from {start:%A %H:%M} to {now:%A %H:%M} (local time)."]
    for s in facts["starts"][:4]:
        lines.append(f"Guardian was stopped at {clock(s['stopped_at'])} and started again at {clock(s['time'])}."
                     if s["stopped_at"] else
                     f"Guardian was started at {clock(s['time'])} and may not have been running before then.")
    lines.append(f"Guardian is {'armed' if facts['armed_now'] else 'disarmed'} now.")
    if facts["arming"]:
        changes = [f"{'armed' if a['armed'] else 'disarmed'} by {'the schedule' if a['by'] == 'schedule' else 'hand'} "
                   f"at {clock(a['time'])}" for a in facts["arming"][-6:]]
        lines.append(f"Arming changes: {c['arming_changes']} ({'; '.join(changes)}).")
    else:
        lines.append("Arming changes logged: none.")
    lines.append(f"Incidents with unrecognised people: {c['incidents']}.")
    lines += [f"- {i['camera'] or 'A camera'} at {clock(i['time'])}, highest {_level(i['peak_level'])}."
              for i in facts["incidents"][:8]]
    if c["incidents"] > 8:
        lines.append(f"- and {c['incidents'] - 8} more.")
    lines.append(f"Unrecognised people detected: {c['unknown_people']}.")
    lines.append(f"Alerts sent to the owner: {c['alerts']}.")
    if facts["panics"]:
        lines.append(f"Panic button pressed at: {', '.join(clock(p) for p in facts['panics'][:4])}.")
    if c["tests"]:
        lines.append(f"Test intrusions run by the owner: {c['tests']}.")
    if facts["insiders"]:
        seen = [f"{p['name']}, {_seen(p)}" for p in facts["insiders"][:6]]
        lines.append(f"Insiders recognised: {c['insiders']} ({'; '.join(seen)}).")
    else:
        lines.append("Insiders recognised: none.")
    if facts["offline"]:
        gone = [f"{o['camera'] or 'a camera'} at {clock(o['time'])}"
                + (f", back after {_duration(o['back_after_seconds'])}" if o["back_after_seconds"] is not None else "")
                for o in facts["offline"][:4]]
        lines.append(f"Cameras that went offline: {c['offline']} ({'; '.join(gone)}).")
    else:
        lines.append("Cameras that went offline: none.")
    lines.append(f"Video clips saved: {c['recordings']}.")
    return "\n".join(lines)


# ---- the template ---------------------------------------------------------------------
def template(facts: dict) -> str:
    """A briefing written straight from the facts, for when the model is unavailable or unreliable."""
    c = facts["counts"]
    ever_armed = facts["armed_at_start"] or any(a["armed"] for a in facts["arming"])
    started = next((s for s in facts["starts"] if not s["stopped_at"]), None)
    if is_quiet(facts):
        span = (f"since Guardian started at {clock(started['time'])}" if started
                else f"over the last {PERIOD_HOURS} hours")
        if not ever_armed:
            return f"Nothing to report {span}, though Guardian was disarmed the whole time, so it raised no alarms."
        return f"All quiet {span}: no unrecognised people, no alerts and no cameras offline."

    sentences = []
    incidents = facts["incidents"]
    if not incidents:
        sentences.append("No unrecognised people were detected." if ever_armed
                         else "Guardian was disarmed the whole time, so it raised no incidents.")
    else:
        def describe(i: dict) -> str:
            peak = i["peak_level"]
            return (f"{i['camera'] or 'a camera'} at {clock(i['time'])} "
                    + (f"(reached level {peak}, {LEVEL_NAMES.get(peak, '?')})" if peak > 1 else "(did not escalate)"))
        people = c["unknown_people"]
        if c["incidents"] == 1:
            # A panic's incident logs no detection, so it may have nobody to count
            who = f"{people} unrecognised people at " if people > 1 else "an unrecognised person at " if people else ""
            sentences.append(f"There was one incident: {who}{describe(incidents[0])}.")
        else:
            what = (f"There were {c['incidents']} incidents with unrecognised people"
                    + (f" ({people} people in all)" if people > c["incidents"] else ""))
            if c["incidents"] <= 3:
                sentences.append(f"{what}: {_join([describe(i) for i in incidents])}.")
            else:
                worst = max(incidents, key=lambda i: i["peak_level"])
                sentences.append(f"{what}; the most serious reached level {worst['peak_level']} "
                                 f"({LEVEL_NAMES.get(worst['peak_level'], '?')}) at {worst['camera'] or 'a camera'} "
                                 f"at {clock(worst['time'])}.")

    alerts = f"you were sent {_plural(c['alerts'], 'alert')}" if c["alerts"] else "no alerts were sent"
    clips = (f"{_plural(c['recordings'], 'clip')} {'was' if c['recordings'] == 1 else 'were'} saved"
             if c["recordings"] else "no clips were saved")
    panic = (f"The panic button was pressed at {_join([clock(p) for p in facts['panics'][:3]])}; "
             if facts["panics"] else "")
    text = f"{panic}{alerts} and {clips}."
    sentences.append(f"{text[0].upper()}{text[1:]}")

    people = facts["insiders"]
    if 0 < c["insiders"] <= 3:
        seen = [f"{p['name']} {'was recognised ' if i == 0 else ''}{_seen(p)}" for i, p in enumerate(people)]
        sentences.append(f"{_join(seen)}.")
    elif c["insiders"] > 3:
        names = [p["name"] for p in people[:5]] + ([f"{c['insiders'] - 5} more"] if c["insiders"] > 5 else [])
        sentences.append(f"{c['insiders']} insiders were recognised: {_join(names)}.")

    offline = facts["offline"]
    if 0 < c["offline"] <= 2:
        parts = [f"{o['camera'] or 'a camera'} went offline at {clock(o['time'])}"
                 + (f" and was back {_duration(o['back_after_seconds'])} later" if o["back_after_seconds"] is not None
                    else "") for o in offline]
        text = "; ".join(parts)
        sentences.append(f"{text[0].upper()}{text[1:]}.")
    elif c["offline"] > 2:
        last = offline[-1]
        sentences.append(f"Cameras went offline {c['offline']} times, most recently {last['camera'] or 'a camera'} "
                         f"at {clock(last['time'])}.")

    # Guardian itself, in one sentence so the briefing stays short
    own = []
    if 0 < c["arming_changes"] <= 2:
        own.append("was " + _join([f"{'armed' if a['armed'] else 'disarmed'} "
                                   f"{'by the schedule' if a['by'] == 'schedule' else 'by hand'} at {clock(a['time'])}"
                                   for a in facts["arming"]]))
    elif c["arming_changes"] > 2:
        own.append(f"was armed or disarmed {c['arming_changes']} times and is "
                   f"{'armed' if facts['armed_now'] else 'disarmed'} now")
    if started:
        own.append(f"was started at {clock(started['time'])} and may not have been running before then")
    gaps = [s for s in facts["starts"] if s["stopped_at"]]
    off = sum((_local(s["time"]) - _local(s["stopped_at"])).total_seconds() for s in gaps)
    if len(gaps) == 1 and off >= 60:
        own.append(f"was off from {clock(gaps[0]['stopped_at'])} to {clock(gaps[0]['time'])}")
    elif off >= 60:
        own.append(f"was stopped {len(gaps)} times, for {_duration(int(off))} in all")
    tests = (f"{_plural(c['tests'], 'test intrusion')} {'was' if c['tests'] == 1 else 'were'} run"
             if c["tests"] else "")
    if own or tests:
        text = "; ".join(filter(None, ["Guardian " + "; it ".join(own) if own else "", tests]))
        sentences.append(f"{text}.")
    return " ".join(sentences)


# ---- the model's version ----------------------------------------------------------------
SYSTEM_PROMPT = (
    "You write the daily briefing of a home security camera system called Guardian, for its owner. "
    "Write 3 to 5 short, calm sentences in plain English, speaking to the owner as you. "
    "Use only the facts you are given: never add a number, time, name, place or event that is not in them, "
    "and do not guess who people were, what they did or why. Put the most important facts first; minor ones may "
    "be left out. No greeting, title, list, emojis or quotation marks."
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LEAD_IN = re.compile(r"^((here is|here's)[^:\n]*|(daily )?briefing):\s*", re.IGNORECASE)
_LIST = re.compile(r"^\s*([-*•]|\d+[.)])\s", re.MULTILINE)
_REFUSAL = re.compile(r"\b(i can(no|')t|i'?m (sorry|not able|unable)|as an ai|language model|i don'?t have|"
                      r"i won'?t|unable to (help|provide))\b", re.IGNORECASE)
_QUIET = re.compile(r"\b(quiet|uneventful|nothing (happened|to report)|no incidents|all clear|calm (day|night))\b",
                    re.IGNORECASE)
# Things the facts can never show; mentioning one would be made up.
_INVENTED = re.compile(r"\b(police|intruders?|burglar\w*|break-?ins?|broke in|broken into|stol(e|en)|theft|thie(f|ves)|damage\w*|"
                       r"injur\w*|fire|smoke|weapons?|guns?|suspicious|deliver\w*|couriers?|postman|parcels?|packages?|"
                       r"cars?|vehicles?|animals?|cats?|dogs?|fox(es)?)\b", re.IGNORECASE)
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "eleven": 11, "twelve": 12, "twice": 2, "dozen": 12, "hundred": 100}
# Capitalised words that are not names, e.g. at the start of a sentence
_COMMON = set("""a after all also although an and another any apart around as at before besides between both but by
day during each earlier either everything finally first for from good here however i if in it its just last later
meanwhile morning afternoon evening night next no nobody none nothing of on one only other otherwise over overall
since so some someone still that the then there these this though throughout to today tonight until we what when
while with yesterday you your am pm""".split())


def clean_briefing(text: str) -> str:
    """Drops markdown, a title and wrapping quotes. Line breaks stay, so a list can be recognised."""
    text = _THINK.sub("", text or "").replace("*", "").replace("#", "")
    text = "\n".join(re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip())
    return _LEAD_IN.sub("", text).strip().strip('"“”').strip()


def _allowed_numbers(facts: str) -> set[int]:
    numbers = {int(n) for n in re.findall(r"\d+", facts)}
    for hour in re.findall(r"\b(\d{1,2}):\d\d\b", facts):  # "14:05" may be written "2:05 pm"
        numbers.add(int(hour) % 12 or 12)
    return numbers


def check_briefing(text: str, facts: str, eventful: bool = True) -> str | None:
    """Why the model's briefing must not be used, or None if it only states what the facts say.
    eventful: there were incidents, alerts or a panic, so the period was not quiet."""
    if _REFUSAL.search(text):
        return "is a refusal, not a briefing"
    if re.search(r"[|<>{}\[\]]", text) or _LIST.search(text):
        return "contains formatting instead of plain sentences"
    words = text.split()
    if len(words) < 12:
        return "is too short"
    if len(words) > 140:
        return "is too long"
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if re.search(r"[A-Za-z]", s)]
    if not 2 <= len(sentences) <= 6:
        return f"has {len(sentences)} sentences instead of 3 to 5"
    allowed = _allowed_numbers(facts)
    for n in re.findall(r"\d+", text):
        if int(n) not in allowed:
            return f"mentions the number {n}, which is not in the facts"
    for word in re.findall(r"[A-Za-z]+", text):
        value = _NUMBER_WORDS.get(word.lower())
        if value is not None and value not in allowed:
            return f"mentions '{word}', a number that is not in the facts"
    known = {w.lower() for w in re.findall(r"[A-Za-z]+", facts + " " + SYSTEM_PROMPT)}
    for word in re.findall(r"\b[A-Z][A-Za-z]*", text):
        lower = word.lower()
        if lower not in known and lower not in _COMMON and not lower.endswith("ly"):
            return f"mentions '{word}', which is not in the facts"
    for m in _INVENTED.finditer(text):
        if m.group(0).lower() not in facts.lower():
            return f"mentions '{m.group(0)}', which is not in the facts"
    if eventful and _QUIET.search(text):
        return "calls the period quiet although there were incidents or alerts"
    return None


# ---- the service --------------------------------------------------------------------------
class BriefingService:
    def __init__(self, settings=settings_service, ai=ai_service, notifier=notification_service, events=event_service,
                 path=BRIEFING_FILE):
        self.settings, self.ai, self.notifier, self.events = settings, ai, notifier, events
        self.path = path
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self.generating = False
        self.briefing: dict | None = None
        self.daily_done: str | None = None  # the date of the last scheduled briefing, e.g. "2026-10-10"
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.briefing = data.get("briefing") if isinstance(data.get("briefing"), dict) else None
            self.daily_done = data.get("daily_done") if isinstance(data.get("daily_done"), str) else None
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as exc:
            print(f"[briefing] Ignoring unreadable {self.path.name}: {exc}")

    def _save(self) -> None:
        with self._save_lock:  # the daily run and the briefing it writes both save
            with self._lock:
                data = {"briefing": self.briefing, "daily_done": self.daily_done}
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                print(f"[briefing] Could not save {self.path.name}: {exc}")

    # ---- writing ------------------------------------------------------------------------
    def _ask_model(self, facts: str, eventful: bool) -> str:
        cfg = self.settings.get().ai
        model = self.ai.resolve_model(cfg)
        timeout = httpx.Timeout(MODEL_TIMEOUT, connect=5)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Facts:\n{facts}\n\nWrite the briefing."}]
        text = clean_briefing(self.ai._chat(cfg, model, messages, temperature=0.4, timeout=timeout, max_tokens=220))
        problem = check_briefing(text, facts, eventful)
        if problem is None:
            return " ".join(text.split())
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That was not acceptable: it {problem}. Write the briefing again "
                                                 "using only the listed facts."}]
        text = clean_briefing(self.ai._chat(cfg, model, messages, temperature=0.2, timeout=timeout, max_tokens=220))
        problem = check_briefing(text, facts, eventful)
        if problem is None:
            return " ".join(text.split())
        raise AIError(f"The model's briefing was rejected: it {problem}")

    def write(self, facts: dict, now: datetime) -> tuple[str, str, str | None]:
        """(text, source, why the template was used)"""
        if is_quiet(facts):
            return template(facts), "template", None
        try:
            c = facts["counts"]
            eventful = bool(c["incidents"] or c["alerts"] or c["panics"])
            return self._ask_model(facts_text(facts, now), eventful), "llm", None
        except (AIError, httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            reason = str(exc) or exc.__class__.__name__
            if isinstance(exc, httpx.TimeoutException):
                reason = f"The model did not answer within {MODEL_TIMEOUT}s"
            elif isinstance(exc, httpx.ConnectError):
                reason = "The language model is not reachable"
            print(f"[briefing] Using the template: {reason}")
            return template(facts), "template", reason

    def generate(self, now: datetime | None = None) -> dict:
        """Collects the facts and writes the briefing. Slow with a model on a CPU: call it in the background."""
        now = (now or datetime.now()).astimezone()
        facts = collect_facts(now, self.settings.get().armed)
        text, source, note = self.write(facts, now)
        briefing = {"text": text, "source": source, "note": note, "generated_at": to_iso(now),
                    "period": {"start": to_iso(now - timedelta(hours=PERIOD_HOURS)), "end": to_iso(now),
                               "hours": PERIOD_HOURS},
                    "facts": facts}
        with self._lock:
            self.briefing = briefing
        self._save()
        return briefing

    def _send(self, briefing: dict) -> None:
        if self.notifier.send_alert("Daily briefing", briefing["text"], "INFO"):
            self.events.log("BRIEFING", "Daily briefing sent to your alert channels")
        else:
            self.events.log("BRIEFING", "Daily briefing written, but not sent: no alert channel is set up", "LOW")

    def refresh(self, send: bool = False) -> bool:
        """Writes a new briefing in the background. Returns False if one is already being written."""
        with self._lock:
            if self.generating:
                return False
            self.generating = True

        def run():
            try:
                briefing = self.generate()
                if send:
                    self._send(briefing)
            except Exception as exc:  # e.g. the database is locked; the old briefing stays
                print(f"[briefing] Could not write the briefing: {exc}")
            finally:
                with self._lock:
                    self.generating = False

        threading.Thread(target=run, daemon=True, name="briefing").start()
        return True

    def run_if_due(self, now: datetime | None = None) -> bool:
        """Called from the manager's background loop: the day's briefing once its time has come, also
        when Guardian was off at that time. Returns True if it was started."""
        cfg = self.settings.get().briefing
        now = (now or datetime.now()).astimezone()
        today = now.date().isoformat()
        if not cfg.enabled or self.daily_done == today or now.strftime("%H:%M") < cfg.time:
            return False
        if not self.refresh(send=cfg.send):
            return False  # one is being written by hand; the daily one follows at the next check
        with self._lock:
            self.daily_done = today
        self._save()
        return True

    def current(self) -> dict:
        """The latest briefing. The first request writes one if there is none yet."""
        enabled = self.settings.get().briefing.enabled
        if self.briefing is None and enabled:
            self.refresh()
        with self._lock:
            briefing = self.briefing or {"text": None, "source": None, "note": None, "generated_at": None,
                                         "period": None, "facts": None}
            return {**briefing, "generating": self.generating, "enabled": enabled}


briefing_service = BriefingService()
