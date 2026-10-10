"""
Incidents and incident reports.

An incident is one camera's response to an unrecognised person (or a panic), from the detection
to its end: "Person left the area", an alarm reset, disarming or the camera stopping. The brain
stores the incident's id with each of its events, so they are grouped without guessing from times.

A report holds only what the event log says: a timeline, up to 8 keyframes from the event
pictures, the clips with their vault status, and a short summary that the local language model
writes from those facts alone. The summary is checked like the spoken warnings are (no numbers,
times, names or things that are not in the facts), retried once, and otherwise replaced by a
template. Summaries are cached until the facts change or the owner asks for a new one.
"""
import base64
import hashlib
import html
import json
import os
import re
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx
from sqlalchemy import and_, func, or_

from config import DATA_DIR
from models.database import SecurityEvent, SessionLocal
from services.ai_service import AIError, ai_service
from services.brain_service import LEVEL_NAMES, SEVERITY
from services.recording_service import recording_library
from services.settings_service import settings_service
from services.vault_service import evidence_vault

SUMMARY_FILE = DATA_DIR / "incident_summaries.json"
MAX_KEYFRAMES = 8
MAX_CACHED = 500
MODEL_TIMEOUT = 120
INCIDENT_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
# Events logged for the whole system, not by a camera, that belong to an incident's time
SYSTEM_EVENTS = ("PANIC", "RESET")

# The texts below are written by brain_service; the report reads them back.
_LEVEL_OF = {severity: level for level, severity in SEVERITY.items()}
_PEAK = re.compile(r"peak level (\d)")
_PEOPLE = re.compile(r"^(\d+) unrecognised people")
_SEEN = re.compile(r"(?:^Returning visitor: |[(;] ?)([^;()]+?), seen before: ")
# How a VOICE event says who wrote the line; the report keeps only the words and where they came from
_VOICE_SOURCES = ((" (via ", "written by the language model"), (" (prepared by ", "written by the language model"),
                  (" (pre-written line", "a pre-written line"), (" (typed by operator)", "typed by the owner"))
_END_REASONS = {"Person left the area": "the person left the area", "Alarm reset": "the alarm was reset",
                "System disarmed": "Guardian was disarmed", "Camera stopped": "the camera stopped"}


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _iso(dt: datetime) -> str:
    return _utc(dt).isoformat().replace("+00:00", "Z")


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()


def clock(iso: str) -> str:
    return f"{_local(iso):%H:%M:%S}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def duration_text(seconds: int) -> str:
    minutes, sec = divmod(max(0, int(seconds)), 60)
    if not minutes:
        return _plural(sec, "second")
    return _plural(minutes, "minute") + (f" {_plural(sec, 'second')}" if sec else "")


def split_voice(description: str) -> tuple[str, str | None]:
    """'Leave now. (via <model>, 900 ms)' -> ('Leave now.', 'written by the language model')"""
    best = None
    for marker, source in _VOICE_SOURCES:
        i = description.rfind(marker)
        if i > 0 and (best is None or i > best[0]):
            best = (i, source)
    return (description[:best[0]], best[1]) if best else (description, None)


def timeline_text(event: dict) -> str:
    kind, text = event["event_type"], event["description"]
    if kind == "VOICE":
        words, source = split_voice(text)
        if source == "typed by the owner":
            return f'Message typed by the owner: "{words}"'
        return f'Warning: "{words}"' + (f" ({source})" if source else "")
    if kind == "RECORDING" and event.get("recording"):
        return f"{text}: {event['recording']}"
    if kind == "CLIP_SAVED" and event.get("recording"):
        return f"{text}: {event['recording']}"
    return text


def build(incident_id: str, events: list[dict], active: bool = False) -> tuple[dict, list[dict]]:
    """The incident and its timeline, from its events (as SecurityEvent.to_dict gives them) plus the
    saved-clip and panic events that belong to it."""
    events = sorted(events, key=lambda e: (e["timestamp"], e["id"]))
    own = [e for e in events if e.get("incident") == incident_id]
    first = own[0] if own else events[0]
    cleared = next((e for e in own if e["event_type"] == "CLEARED"), None)
    detection = next((e for e in own if e["event_type"] == "DETECTION"), None)
    levels = [_LEVEL_OF.get(e["severity"], 0) for e in own if e["event_type"] in ("DETECTION", "ESCALATION")]
    if cleared and (m := _PEAK.search(cleared["description"])):
        levels.append(int(m.group(1)))
    peak = max(levels, default=0)
    unknown = 0
    if detection:
        m = _PEOPLE.match(detection["description"])
        unknown = int(m.group(1)) if m else 1
    insiders = sorted({e["description"].removeprefix("Recognised ") for e in own
                       if e["event_type"] == "INSIDER" and e["description"].startswith("Recognised ")})
    visitors: list[str] = []
    for e in own:
        if e["event_type"] in ("DETECTION", "VISITOR"):
            for m in _SEEN.finditer(e["description"]):
                if m.group(1).strip() not in visitors:
                    visitors.append(m.group(1).strip())
    clips: list[str] = []
    for e in events:
        if e.get("recording") and e["recording"] not in clips:
            clips.append(e["recording"])
    end = cleared["timestamp"] if cleared else None
    last = (end or own[-1]["timestamp"]) if own else first["timestamp"]
    timeline = [{"time": e["timestamp"], "type": e["event_type"], "severity": e["severity"], "text": timeline_text(e),
                 "event_id": e["id"], "recording": e.get("recording"), "snapshot": e.get("snapshot")} for e in events]
    incident = {
        "id": incident_id,
        "camera": next((e["camera"] for e in own if e.get("camera")), None),
        "start": first["timestamp"],
        "end": end,
        "status": "ended" if cleared else "ongoing" if active else "unfinished",
        "end_reason": cleared["description"].split(". Incident lasted")[0] if cleared else None,
        "duration_seconds": int((_local(last) - _local(first["timestamp"])).total_seconds()),
        "peak_level": peak,
        "peak_label": LEVEL_NAMES.get(peak),
        "test": bool(detection and "(test)" in detection["description"]),
        "panic": any(e["event_type"] == "PANIC" for e in events),
        "people": {"unknown": unknown, "insiders": insiders, "visitors": visitors},
        "clips": clips,
        "pictures": sum(1 for e in own if e.get("snapshot")),
        "warnings": sum(1 for e in own if e["event_type"] == "VOICE"),
        "alerts": sum(1 for e in own if e["event_type"] == "ALERT"),
        "siren": any(e["event_type"] == "SIREN" for e in own),
    }
    return incident, timeline


def facts_text(incident: dict, timeline: list[dict]) -> str:
    """Everything the summary may say, in the Guardian computer's local time."""
    start = _local(incident["start"])
    people = incident["people"]
    lines = [f"Camera: {incident['camera'] or 'unnamed'}",
             f"Date: {start:%A} {start.day} {start:%B %Y}",
             f"Started: {clock(incident['start'])}"]
    if incident["end"]:
        lines.append(f"Ended: {clock(incident['end'])} ({incident['end_reason']})")
    else:
        lines.append("Ended: not yet" if incident["status"] == "ongoing" else "Ended: no end was recorded")
    lines.append(f"Length: {duration_text(incident['duration_seconds'])}")
    if incident["peak_level"]:
        lines.append(f"Highest level reached: {incident['peak_level']} ({incident['peak_label']})")
    if incident["test"]:
        lines.append("This was a test intrusion started from the dashboard, not a real person.")
    if incident["panic"]:
        lines.append("The alarm was raised by hand from the dashboard (panic button).")
    if people["unknown"]:
        lines.append(f"Unrecognised people when first detected: {people['unknown']}")
    lines.append(f"Recognised household members or staff: {', '.join(people['insiders']) or 'none'}")
    lines.append(f"Remembered visitors: {', '.join(people['visitors']) or 'none'}")
    lines.append(f"Warnings: {incident['warnings']}")
    lines.append(f"Owner alerts sent: {incident['alerts']}")
    lines.append(f"Siren: {'sounded' if incident['siren'] else 'did not sound'}")
    lines.append(f"Clips recorded: {len(incident['clips'])}")
    lines.append("Timeline:")
    lines += [f"{clock(t['time'])} {t['text']}" for t in timeline]
    return "\n".join(lines)


def template_summary(incident: dict) -> str:
    camera, start = incident["camera"] or "the camera", clock(incident["start"])
    unknown = incident["people"]["unknown"]
    if unknown:
        who = "An unrecognised person was" if unknown == 1 else f"{unknown} unrecognised people were"
        first = f"{who} detected on {camera} at {start}{' during a test' if incident['test'] else ''}."
    else:
        first = f"The alarm was raised from the dashboard, starting an incident on {camera} at {start}."
    actions = []
    if incident["warnings"]:
        actions.append(f"gave {_plural(incident['warnings'], 'warning')}")
    if incident["alerts"]:
        actions.append("alerted the owner")
    if incident["siren"]:
        actions.append("sounded the siren")
    if incident["clips"]:
        actions.append(f"recorded {_plural(len(incident['clips']), 'clip')}")
    second = f"The incident reached level {incident['peak_level']} ({incident['peak_label']})"
    if actions:
        second += "; Guardian " + (", ".join(actions[:-1]) + " and " + actions[-1] if len(actions) > 1 else actions[0])
    sentences = [first, second + "."]
    if incident["status"] == "ended":
        reason = _END_REASONS.get(incident["end_reason"], (incident["end_reason"] or "it ended").lower())
        sentences.append(f"It ended at {clock(incident['end'])}, when {reason}, after "
                         f"{duration_text(incident['duration_seconds'])}.")
    elif incident["status"] == "ongoing":
        sentences.append("It is still going on.")
    else:
        sentences.append("The event log has no end for this incident; Guardian may have stopped during it.")
    return " ".join(sentences)


# ---- checking the model's summary ----------------------------------------------------------
SYSTEM_PROMPT = (
    "You write incident summaries for a home security camera log. Use only the facts you are given. "
    "Write 2 to 4 calm, factual sentences of plain text: no title, no list, no markdown. "
    "Say what happened in order: when the person was detected, the highest level, the warnings, alerts, siren "
    "and recordings, and how it ended. Do not guess who the person was, what they wanted, what they looked like "
    "or how they moved. Do not add numbers, times or names that are not in the facts.")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LEAD_IN = re.compile(r"^(here is|here's|summary|incident summary)[^:\n]*:\s*", re.IGNORECASE)
_REFUSAL = re.compile(r"\b(i can(no|')t|i'?m (not able|unable)|as an ai|language model)\b", re.IGNORECASE)
_LIST = re.compile(r"(^|\n)\s*([-•]|\d+[.)])\s")
# Things a summary must not add unless the facts say so
_INVENTED = re.compile(r"\b(police|officers?|guards?|dogs?|weapons?|knife|knives|guns?|armed with|stole|stolen|steal"
                       r"|theft|thief|burglar\w*|broke|break-in|forced|damaged?|vehicles?|cars?|masked|hood\w*"
                       r"|cloth\w*|wearing|dark|tall|man|woman|male|female|boy|girl|suspicious|fled|ran|escaped"
                       r"|attempted|tried|window|door|latch|gate|fence)\b", re.IGNORECASE)
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "eleven": 11, "twelve": 12, "twice": 2, "dozen": 12, "hundred": 100}
_TIME = re.compile(r"\b(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\b")
# Capitalised words that are not names, e.g. at the start of a sentence
_COMMON = set("""a after all also although an and another any as at before both but by during each either finally
first for from guardian here however i if in it its just later meanwhile next no nobody none nothing of on once one
only shortly since so some someone soon still that the then there these this though throughout to until when while
with within at am pm level levels warning warnings""".split())


def clean_summary(text: str) -> str:
    text = _THINK.sub("", text or "").replace("*", "").replace("#", "")
    text = "\n".join(re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip())
    return _LEAD_IN.sub("", text).strip().strip('"“”').strip()


def _allowed_numbers(facts: str) -> set[int]:
    numbers = {int(n) for n in re.findall(r"\d+", facts)}
    for hour in re.findall(r"\b(\d{1,2}):\d\d\b", facts):  # "14:05" may be written "2:05 pm"
        numbers.add(int(hour) % 12 or 12)
    return numbers


def check_summary(text: str, facts: str) -> str | None:
    """Why the model's summary must not be used, or None if it only states what the facts say."""
    if _REFUSAL.search(text):
        return "is a refusal, not a summary"
    if re.search(r"[|<>{}\[\]]", text) or _LIST.search(text):
        return "contains formatting instead of plain sentences"
    words = text.split()
    if len(words) < 12:
        return "is too short"
    if len(words) > 110:
        return "is too long"
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if re.search(r"[A-Za-z]", s)]
    if not 2 <= len(sentences) <= 4:
        return f"has {len(sentences)} sentences instead of 2 to 4"
    fact_times = {(int(h), int(m), s and int(s)) for h, m, s in _TIME.findall(facts)}
    for h, m, s in _TIME.findall(text):
        h, m, s = int(h), int(m), s and int(s)
        if not any(m == fm and h % 12 == fh % 12 and (not s or s == fs) for fh, fm, fs in fact_times):
            return f"mentions the time {h}:{m:02d}, which is not in the facts"
    allowed = _allowed_numbers(facts)
    for n in re.findall(r"\d+", _TIME.sub(" ", text)):
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
        if not re.search(rf"\b{re.escape(m.group(0))}\b", facts, re.IGNORECASE):
            return f"mentions '{m.group(0)}', which is not in the facts"
    return None


def pick_keyframes(items: list[dict], n: int = MAX_KEYFRAMES) -> list[dict]:
    """Up to n pictures spread over the incident, always the first and the last."""
    if len(items) <= n:
        return items
    picks = sorted({round(i * (len(items) - 1) / (n - 1)) for i in range(n)})
    return [items[i] for i in picks]


class IncidentService:
    def __init__(self, settings=settings_service, ai=ai_service, vault=evidence_vault, library=recording_library,
                 cache_file: Path = SUMMARY_FILE):
        self.settings, self.ai, self.vault, self.library = settings, ai, vault, library
        self.cache_file = cache_file
        # Ids of incidents happening right now; set by the API from the cameras' brains
        self.active: Callable[[], set[str]] = set
        self._lock = threading.Lock()
        self._writing: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="incident-summary")
        self._cache: dict[str, dict] = {}
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            self._cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            pass

    # ---- reading incidents from the event log ------------------------------------------------
    def recent(self, limit: int = 50, recording: str | None = None) -> list[dict]:
        db = SessionLocal()
        try:
            q = db.query(SecurityEvent.incident, func.min(SecurityEvent.timestamp).label("start")) \
                .filter(SecurityEvent.incident.isnot(None))
            if recording:
                q = q.filter(SecurityEvent.recording == recording)
            ids = [i for i, _ in q.group_by(SecurityEvent.incident).order_by(func.min(SecurityEvent.timestamp).desc())
                   .limit(limit).all()]
            rows = db.query(SecurityEvent).filter(SecurityEvent.incident.in_(ids)).all() if ids else []
        finally:
            db.close()
        grouped: dict[str, list[dict]] = {i: [] for i in ids}
        for row in rows:
            grouped[row.incident].append(row.to_dict())
        active = self.active()
        return [build(i, events, i in active)[0] for i, events in grouped.items() if events]

    def get(self, incident_id: str) -> tuple[dict, list[dict]] | None:
        if not INCIDENT_ID.match(incident_id or ""):
            return None
        db = SessionLocal()
        try:
            own = db.query(SecurityEvent).filter(SecurityEvent.incident == incident_id).all()
            if not own:
                return None
            clips = {e.recording for e in own if e.recording}
            start = min(e.timestamp for e in own) - timedelta(seconds=2)
            end = max(e.timestamp for e in own) + timedelta(seconds=2)
            extra = db.query(SecurityEvent).filter(or_(
                and_(SecurityEvent.event_type == "CLIP_SAVED", SecurityEvent.recording.in_(clips)),
                and_(SecurityEvent.event_type.in_(SYSTEM_EVENTS), SecurityEvent.camera.is_(None),
                     SecurityEvent.timestamp >= start, SecurityEvent.timestamp <= end))).all()
            events = {e.id: e.to_dict() for e in own + extra}
        finally:
            db.close()
        return build(incident_id, list(events.values()), incident_id in self.active())

    # ---- the summary -----------------------------------------------------------------------
    def _ask_model(self, facts: str) -> str:
        cfg = self.settings.get().ai
        model = self.ai.resolve_model(cfg)
        timeout = httpx.Timeout(MODEL_TIMEOUT, connect=5)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Facts:\n{facts}\n\nWrite the summary."}]
        text = clean_summary(self.ai._chat(cfg, model, messages, temperature=0.3, timeout=timeout, max_tokens=200))
        problem = check_summary(text, facts)
        if problem is None:
            return " ".join(text.split())
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That was not acceptable: it {problem}. Write the summary again "
                                                 "using only the facts."}]
        text = clean_summary(self.ai._chat(cfg, model, messages, temperature=0.2, timeout=timeout, max_tokens=200))
        problem = check_summary(text, facts)
        if problem is None:
            return " ".join(text.split())
        raise AIError(f"The model's summary was rejected: it {problem}")

    def write_summary(self, facts: str, template: str) -> tuple[str, str, str | None]:
        """(text, "llm" or "template", why the template was used)"""
        try:
            return self._ask_model(facts), "llm", None
        except (AIError, httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            reason = str(exc) or exc.__class__.__name__
            if isinstance(exc, httpx.TimeoutException):
                reason = f"The language model did not answer within {MODEL_TIMEOUT}s"
            elif isinstance(exc, httpx.ConnectError):
                reason = "The language model is not reachable"
            print(f"[incident] Using the template summary: {reason}")
            return template, "template", reason

    def _save_cache(self) -> None:
        with self._lock:
            data = dict(list(self._cache.items())[-MAX_CACHED:])
            self._cache = data
        try:
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.cache_file)
        except OSError as exc:
            print(f"[incident] Could not save summaries: {exc}")

    def summary(self, incident: dict, facts: str, regenerate: bool = False) -> dict:
        """The cached summary, or the template while the model writes one in the background."""
        template = template_summary(incident)
        if incident["status"] != "ended":
            return {"text": template, "source": "template", "reason": "The incident has not ended yet.",
                    "pending": False, "written_at": None}
        digest = hashlib.sha256(facts.encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(incident["id"])
            if cached and cached.get("facts") == digest and not regenerate and incident["id"] not in self._writing:
                return {k: cached.get(k) for k in ("text", "source", "reason", "written_at")} | {"pending": False}
            busy = self.active()
            if busy:
                # The model is needed for spoken warnings; write the summary when things are quiet.
                return {"text": template, "source": "template", "pending": False, "written_at": None,
                        "reason": "An incident is in progress, so the language model is kept free for warnings. "
                                  "Open the report again later for a written summary."}
            if incident["id"] not in self._writing:
                self._writing.add(incident["id"])
                self._executor.submit(self._write, incident["id"], facts, template, digest)
        return {"text": template, "source": "template", "reason": None, "pending": True, "written_at": None}

    def _write(self, incident_id: str, facts: str, template: str, digest: str) -> None:
        try:
            text, source, reason = self.write_summary(facts, template)
            with self._lock:
                self._cache.pop(incident_id, None)  # re-inserted last, so the oldest are dropped first
                self._cache[incident_id] = {"facts": digest, "text": text, "source": source, "reason": reason,
                                            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
                                            .replace("+00:00", "Z")}
            self._save_cache()
        finally:
            with self._lock:
                self._writing.discard(incident_id)

    def cached_summary(self, incident: dict, facts: str) -> dict:
        """For the evidence package: what the report page shows now, without waiting for the model."""
        digest = hashlib.sha256(facts.encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(incident["id"])
        if incident["status"] == "ended" and cached and cached.get("facts") == digest:
            return {k: cached.get(k) for k in ("text", "source", "reason", "written_at")}
        return {"text": template_summary(incident), "source": "template", "reason": None, "written_at": None}

    # ---- the report ----------------------------------------------------------------------
    def _evidence(self, clips: list[str], keyframes: list[dict]) -> list[dict]:
        items = []
        for kind, name, event_id in [("clip", c, None) for c in clips] + \
                                     [("picture", k["snapshot"], k["event_id"]) for k in keyframes]:
            if kind == "clip" and name in self.library.in_progress:
                items.append({"kind": kind, "name": name, "status": "recording", "exists": False, "size": None})
                continue
            try:
                result = self.vault.verify(kind, name)
            except ValueError:
                continue
            path = self.vault.folders[kind] / name
            size = path.stat().st_size if result["exists"] else result["size"]
            items.append({**result, "size": size, "event_id": event_id})
        return items

    def summary_of(self, incident_id: str, regenerate: bool = False) -> dict | None:
        found = self.get(incident_id)
        if not found:
            return None
        incident, timeline = found
        return self.summary(incident, facts_text(incident, timeline), regenerate)

    def report(self, incident_id: str) -> dict | None:
        found = self.get(incident_id)
        if not found:
            return None
        incident, timeline = found
        facts = facts_text(incident, timeline)
        keyframes = pick_keyframes([t for t in timeline if t["snapshot"]])
        return {
            "incident": incident,
            "timeline": timeline,
            "facts": facts,
            "summary": self.summary(incident, facts),
            "keyframes": keyframes,
            "evidence": self._evidence(incident["clips"], keyframes),
            "vault": {"fingerprint": self.vault.fingerprint},
        }

    def package(self, incident_id: str) -> tuple[Path, str] | None:
        """Writes the evidence package to a temporary ZIP file next to the clips; the caller deletes it."""
        report = self.report(incident_id)
        if report is None:
            return None
        incident = report["incident"]
        report["summary"] = self.cached_summary(incident, report["facts"])
        folders = self.vault.folders
        pictures = {}
        for k in report["keyframes"]:
            try:
                pictures[k["snapshot"]] = (folders["picture"] / k["snapshot"]).read_bytes()
            except OSError:
                pass
        page = render_html(report, pictures).encode()
        sealed = self.vault.seal_report(page, incident["id"])
        files = {("clip", c) for c in incident["clips"]} | {("picture", name) for name in pictures}
        lines = self.vault.lines_for(files) + [json.dumps(sealed, sort_keys=True, separators=(",", ":"))]
        fd, tmp = tempfile.mkstemp(prefix=".package-", suffix=".zip", dir=DATA_DIR)
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
                z.writestr("report.html", page)
                for clip in incident["clips"]:
                    for path in (folders["clip"] / clip, (folders["clip"] / clip).with_suffix(".json")):
                        if path.is_file():  # videos are already compressed; store them as they are
                            z.write(path, f"clips/{path.name}", compress_type=zipfile.ZIP_STORED)
                for name in pictures:
                    z.write(folders["picture"] / name, f"keyframes/{name}", compress_type=zipfile.ZIP_STORED)
                z.writestr("ledger-entries.jsonl", "\n".join(lines) + "\n")
                z.writestr("public-key.pem", self.vault.public_pem)
                z.writestr("VERIFY.txt", verify_text(incident, self.vault.fingerprint))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return Path(tmp), f"guardian-incident-{incident['id']}.zip"


VERIFY_SCRIPT = '''import hashlib, json, pathlib
from cryptography.hazmat.primitives.serialization import load_pem_public_key

key = load_pem_public_key(pathlib.Path("public-key.pem").read_bytes())
folders = {"clip": "clips", "picture": "keyframes", "report": "."}
for line in pathlib.Path("ledger-entries.jsonl").read_text().splitlines():
    entry = json.loads(line)
    signature = bytes.fromhex(entry.pop("sig"))
    # Raises InvalidSignature if the entry was not signed by this key or was changed
    key.verify(signature, json.dumps(entry, sort_keys=True, separators=(",", ":")).encode())
    if entry["kind"] not in folders:
        print("SIGNED ", entry["seq"], entry["kind"], entry["name"], entry.get("reason", ""))
        continue
    path = pathlib.Path(folders[entry["kind"]], entry["name"])
    ok = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    if ok and entry["meta_sha256"]:
        meta = path.with_suffix(".json")
        ok = meta.is_file() and hashlib.sha256(meta.read_bytes()).hexdigest() == entry["meta_sha256"]
    print("OK     " if ok else "CHANGED", entry["seq"], entry["name"])
'''


def verify_text(incident: dict, fingerprint: str) -> str:
    start = _local(incident["start"])
    return f"""Evidence package for incident {incident['id']}
Camera {incident['camera'] or 'unnamed'}, {start:%d %B %Y}, started {start:%H:%M:%S} (Guardian computer's time)

Guardian fingerprinted (SHA-256) each clip and picture here when it saved it, and wrote the
fingerprint to its ledger, signed with this Guardian's private key. ledger-entries.jsonl holds
those signed entries; public-key.pem is the key that checks them.

Public key fingerprint (SHA-256 of the raw key): {fingerprint}

What this proves: the files have not changed since Guardian saved them, as long as the private
key stayed private. Someone with full access to the Guardian computer could sign altered files
with the same key, so compare the fingerprint above with one you noted down earlier or keep
elsewhere. Entries marked "sealed_late" were sealed after the fact, when Guardian next started.
Each entry's "prev" is the SHA-256 of the line before it in Guardian's full ledger, which Guardian
checks with Verify all (Settings, System).

To check, install Python 3 and the cryptography package (pip install cryptography), save the
script below as verify.py in the folder where you unzipped this package, and run it there
(python verify.py). Each file should print OK.

{VERIFY_SCRIPT}"""


# ---- the self-contained report.html ---------------------------------------------------------
STATUS_TEXT = {"intact": "Intact", "modified": "Modified after it was sealed", "missing": "Missing",
               "not_sealed": "Not sealed", "ledger_broken": "Ledger damaged", "recording": "Still recording"}


def _size(n: int | None) -> str:
    if not n:
        return "–"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" or n >= 10 else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _when(iso: str | None) -> str:
    return f"{_local(iso):%d %b %Y %H:%M:%S}" if iso else "–"


def render_html(report: dict, pictures: dict[str, bytes]) -> str:
    inc, e = report["incident"], html.escape
    start = _local(inc["start"])
    rows = "".join(f"<tr><td class=t>{clock(t['time'])}</td><td>{e(t['text'])}</td></tr>" for t in report["timeline"])
    frames = "".join(
        f"<figure><img alt='' src='data:image/jpeg;base64,{base64.b64encode(pictures[k['snapshot']]).decode()}'>"
        f"<figcaption>{clock(k['time'])} · {e(k['text'])}</figcaption></figure>"
        for k in report["keyframes"] if k["snapshot"] in pictures)
    evidence = "".join(
        f"<tr><td>{e(x['name'])}</td><td>{_size(x.get('size'))}</td><td class=h>{e(x.get('sha256') or '–')}</td>"
        f"<td>{_when(x.get('sealed_at'))}{' (late)' if x.get('sealed_late') else ''}</td>"
        f"<td>{STATUS_TEXT.get(x['status'], x['status'])}</td></tr>" for x in report["evidence"])
    summary = report["summary"]
    source = ("Written by the local language model from the facts in this report, and checked against them."
              if summary["source"] == "llm" else "Written from a template from the facts in this report.")
    end = f"{clock(inc['end'])} ({e(inc['end_reason'] or '')})" if inc["end"] else "no end recorded"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Incident report · {e(inc['camera'] or '')} · {start:%d %b %Y %H:%M}</title>
<style>
  body {{ font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: #111; background: #fff;
         max-width: 900px; margin: 24px auto; padding: 0 16px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 16px; margin: 24px 0 8px; }}
  .meta {{ color: #444; }} .note {{ color: #555; font-size: 12px; }}
  table {{ width: 100%; border-collapse: collapse; }} td, th {{ text-align: left; padding: 4px 6px;
         border-bottom: 1px solid #ddd; vertical-align: top; }} tr {{ break-inside: avoid; }}
  .t {{ white-space: nowrap; font-family: ui-monospace, monospace; }}
  .h {{ font-family: ui-monospace, monospace; font-size: 11px; word-break: break-all; }}
  .frames {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }}
  figure {{ margin: 0; break-inside: avoid; }} img {{ width: 100%; border: 1px solid #ccc; }}
  figcaption {{ font-size: 12px; color: #444; }}
</style></head><body>
<h1>Incident report: {e(inc['camera'] or 'camera')}</h1>
<p class=meta>{start:%A %d %B %Y}, {clock(inc['start'])} to {end} · {duration_text(inc['duration_seconds'])}
 · peak level {inc['peak_level']} ({e(inc['peak_label'] or '')}){' · test' if inc['test'] else ''}</p>
<p class=note>Times are the Guardian computer's local time. Incident {e(inc['id'])}.</p>
<h2>Summary</h2><p>{e(summary['text'])}</p><p class=note>{source}</p>
<h2>Timeline</h2><table>{rows}</table>
<h2>Keyframes</h2><div class=frames>{frames or '<p>No pictures.</p>'}</div>
<h2>Evidence</h2>
<table><tr><th>File</th><th>Size</th><th>SHA-256</th><th>Sealed</th><th>When this report was made</th></tr>
{evidence}</table>
<h2>How to verify</h2>
<p>Guardian fingerprinted each file when it saved it and signed the fingerprint into its ledger with its private key.
VERIFY.txt in the evidence package explains how to check the files against ledger-entries.jsonl and public-key.pem
(fingerprint {e(report['vault']['fingerprint'])}). This shows the files have not changed since Guardian saved them, as
long as the private key stayed private; someone with full access to the Guardian computer could sign altered files.</p>
</body></html>"""


incident_service = IncidentService()
