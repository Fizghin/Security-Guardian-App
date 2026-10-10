"""
Daily security digest: what happened in the last 24 hours, sent once a day at a set time
through the configured notification channels (Telegram, ntfy, e-mail…).
"""
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from config import DATA_DIR
from services.evidence_service import evidence_vault
from services.insights_service import insights
from services.recording_service import recording_library


def build(now: datetime | None = None, vault=evidence_vault, library=recording_library, make_insights=insights) -> dict:
    now = (now or datetime.now()).astimezone()
    ins = make_insights(14, now=now)
    day = ins["last_24h"]
    since = now - timedelta(hours=24)
    clips = []
    for r in library.list():
        try:
            started = datetime.fromisoformat(r["started"])
        except (TypeError, ValueError):
            continue
        if started.tzinfo is None:
            started = started.astimezone()
        if started >= since:
            clips.append(r)
    chain = vault.verify_chain()
    quiet = not (day["incidents"] or day["alerts"] or day["sirens"] or day["sounds"] or clips)

    lines = []
    if quiet:
        lines.append("All quiet: no incidents in the last 24 hours.")
    else:
        lines.append(f"Incidents: {day['incidents']}" + (f" ({day['night_incidents']} at night)" if day["night_incidents"] else ""))
        if day["alerts"]:
            lines.append(f"Owner alerts: {day['alerts']}")
        if day["sirens"]:
            lines.append(f"Siren sounded: {day['sirens']} time{'s' if day['sirens'] > 1 else ''}")
        if day["sounds"]:
            lines.append(f"Loud sounds: {day['sounds']}")
        if clips:
            peak = max((c.get("max_level") or 0) for c in clips)
            lines.append(f"Clips recorded: {len(clips)}" + (f", peak level {peak}" if peak else ""))
    threat = ins["threat"]
    lines.append(f"Threat score: {threat['score']}/100 ({threat['level']})")
    lines.extend(f"• {r}" for r in threat["reasons"][:3])
    alerts = [f["text"] for f in ins["findings"] if f["kind"] in ("alert", "warning")]
    lines.extend(f"! {t}" for t in alerts[:2])
    lines.append("Evidence vault: " + (f"intact, {len(vault.sealed_files())} clips sealed" if chain["ok"]
                                       else "CHAIN BROKEN, check the dashboard"))
    title = f"Guardian daily digest · {now:%a %d %b}"
    severity = "HIGH" if threat["level"] == "high" or not chain["ok"] else "MEDIUM" if threat["level"] == "elevated" \
        else "INFO"
    return {"title": title, "message": "\n".join(lines), "severity": severity, "quiet": quiet,
            "threat": threat, "generated": now.isoformat()}


class DigestScheduler:
    def __init__(self, state_file: Path = DATA_DIR / "digest_state.json"):
        self.state_file = state_file
        self._lock = threading.Lock()

    def _last_sent(self) -> str | None:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8")).get("last_sent")
        except (OSError, ValueError, AttributeError):
            return None

    def _mark(self, day: str) -> None:
        try:
            self.state_file.write_text(json.dumps({"last_sent": day}), encoding="utf-8")
        except OSError as exc:
            print(f"[digest] Could not save state: {exc}")

    def due(self, cfg, now: datetime) -> bool:
        if not cfg.enabled:
            return False
        hh, mm = (int(x) for x in cfg.time.split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        # Only within 6 hours after the set time: a computer that was off all morning doesn't send it at night.
        return at <= now < at + timedelta(hours=6) and self._last_sent() != now.date().isoformat()

    def check(self, cfg, notifier, events, now: datetime | None = None, build_digest=build) -> bool:
        now = (now or datetime.now()).astimezone()
        with self._lock:
            if not self.due(cfg, now):
                return False
            self._mark(now.date().isoformat())  # before sending: a failing channel must not resend every 10 s
        digest = build_digest(now)
        if digest["quiet"] and cfg.skip_quiet:
            return False
        sent = notifier.send_alert(digest["title"], digest["message"], digest["severity"])
        events.log("NOTIFICATION", f"Daily digest {'sent' if sent else 'could not be sent'}", "INFO" if sent else "MEDIUM")
        return sent


digest_scheduler = DigestScheduler()
