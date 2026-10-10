"""
Incident reports: a forensic summary of one recorded incident.

For a saved clip, the events its camera logged around the clip's time are collected
into a timeline (detection, each escalation, the warnings spoken, alerts, the siren,
when it cleared), summarised in plain language, and paired with the clip's
evidence-vault check (its SHA-256 and whether the file is unchanged since it was
sealed). The HTML version is laid out for printing or saving as a PDF.
"""
import html
import re
from datetime import datetime, timedelta, timezone

from models.database import SecurityEvent, SessionLocal, to_iso
from services.evidence_service import evidence_vault
from services.recording_service import recording_library

BEFORE_SECONDS = 30  # events this long before the clip starts still belong to it (pre-roll, the detection)
AFTER_SECONDS = 20
LEVEL_NAMES = {1: "Person detected", 2: "Loitering", 3: "Intruder", 4: "Alarm"}
REASONS = {"intruder": "Intrusion", "panic": "Panic alarm", "test": "Test", "sound": "Loud sound"}


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _local(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc).astimezone()


def build(file: str, vault=evidence_vault, library=recording_library, session_factory=SessionLocal) -> dict:
    rec = next((r for r in library.list() if r["file"] == file), None)
    if rec is None:
        raise FileNotFoundError(file)
    started = datetime.fromisoformat(rec["started"])
    if started.tzinfo is None:
        started = started.astimezone()
    duration = float(rec.get("duration") or 0)
    ended = started + timedelta(seconds=duration)
    window_start = _utc_naive(started) - timedelta(seconds=BEFORE_SECONDS)
    window_end = _utc_naive(ended) + timedelta(seconds=AFTER_SECONDS)

    db = session_factory()
    try:
        q = db.query(SecurityEvent).filter(SecurityEvent.timestamp >= window_start,
                                           SecurityEvent.timestamp <= window_end)
        if rec.get("camera"):
            q = q.filter((SecurityEvent.camera == rec["camera"]) | (SecurityEvent.recording == file))
        rows = q.order_by(SecurityEvent.timestamp.asc(), SecurityEvent.id.asc()).all()
        events = [r.to_dict() for r in rows]
    finally:
        db.close()

    t0 = _utc_naive(started)
    timeline = []
    for e in events:
        ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        timeline.append({**e, "offset": round((_utc_naive(ts) - t0).total_seconds(), 1),
                         "local_time": ts.astimezone().strftime("%H:%M:%S")})

    stats = _stats(timeline, rec)
    integrity = vault.verify_clip(file)
    return {
        "file": file,
        "camera": rec.get("camera") or "Unknown camera",
        "reason": rec.get("reason"),
        "reason_label": REASONS.get(rec.get("reason") or "", rec.get("reason") or "Incident"),
        "started": started.isoformat(),
        "ended": ended.isoformat(),
        "duration": duration,
        "max_level": rec.get("max_level") or 0,
        "summary": _summary(rec, started, timeline, stats, integrity),
        "stats": stats,
        "timeline": timeline,
        "integrity": integrity,
        "fingerprint": vault.fingerprint(),
        "generated": to_iso(datetime.now(timezone.utc)),
    }


def _stats(timeline: list[dict], rec: dict) -> dict:
    types = [e["event_type"] for e in timeline]
    lasted = None
    for e in timeline:
        if e["event_type"] == "CLEARED":
            m = re.search(r"lasted (\d+)s", e["description"] or "")
            if m:
                lasted = int(m.group(1))
    levels = [(e["offset"], int(m.group(1))) for e in timeline if e["event_type"] == "ESCALATION"
              for m in [re.search(r"Threat level (\d)", e["description"] or "")] if m]
    detected = next((e for e in timeline if e["event_type"] in ("DETECTION", "PANIC", "SOUND")), None)
    return {
        "warnings": types.count("VOICE"),
        "alerts": types.count("ALERT"),
        "siren": "SIREN" in types,
        "returning_visitor": any("seen before" in (e["description"] or "") or e["event_type"] == "VISITOR"
                                 for e in timeline),
        "insiders": sorted({e["description"].replace("Recognised ", "") for e in timeline
                            if e["event_type"] == "INSIDER" and e["description"]}),
        "lasted": lasted,
        "first_seen": detected["local_time"] if detected else None,
        "escalations": [{"offset": o, "level": lv, "label": LEVEL_NAMES.get(lv, "")} for o, lv in levels],
        "pictures": sum(1 for e in timeline if e.get("snapshot")),
        "peak_level": rec.get("max_level") or max((lv for _, lv in levels), default=0),
    }


def _summary(rec: dict, started: datetime, timeline: list[dict], stats: dict, integrity: dict) -> str:
    when = started.strftime("%a %d %b %Y at %H:%M:%S")
    camera = rec.get("camera") or "an unnamed camera"
    detection = next((e for e in timeline if e["event_type"] == "DETECTION"), None)
    reason = rec.get("reason")
    if reason == "panic":
        opening = f"On {when}, the panic alarm was raised and {camera} recorded the scene."
    elif reason == "sound":
        opening = f"On {when}, {camera} heard a loud sound and started recording."
    elif detection:
        who = detection["description"].split(" detected")[0].lower()
        opening = f"On {when}, {'an ' if who.startswith('unrec') else ''}{who} was detected at {camera}."
    else:
        opening = f"On {when}, {camera} recorded an incident."
    if reason == "test":
        opening += " This was a test."

    parts = [opening]
    if stats["returning_visitor"]:
        parts.append("Guardian recognised this person as a returning visitor.")
    if stats["escalations"]:
        last = stats["escalations"][-1]
        parts.append(f"It escalated to level {last['level']} ({last['label']}) "
                     f"{_after(last['offset'], timeline)}.")
    actions = []
    if stats["warnings"]:
        actions.append(f"spoke {stats['warnings']} warning{'s' if stats['warnings'] > 1 else ''}")
    if stats["alerts"]:
        actions.append("alerted the owner")
    if stats["siren"]:
        actions.append("sounded the siren")
    if actions:
        parts.append(f"Guardian {_join(actions)}.")
    if stats["lasted"] is not None:
        parts.append(f"The incident ended after {stats['lasted']} s.")
    parts.append(f"The clip is {rec.get('duration') or 0:.0f} s long, including the seconds before the trigger.")
    status = integrity.get("status")
    if status == "verified":
        parts.append(f"Its integrity is verified: SHA-256 {integrity['sha256'][:16]}… matches the sealed record.")
    elif status == "tampered":
        parts.append("WARNING: the clip does not match its sealed record and may have been altered.")
    elif status == "unsealed":
        parts.append("The clip was not sealed in the evidence vault, so its integrity can't be proven.")
    return " ".join(parts)


def _after(offset: float, timeline: list[dict]) -> str:
    detection = next((e for e in timeline if e["event_type"] in ("DETECTION", "PANIC")), None)
    if detection is None:
        return "during the clip"
    seconds = max(0, round(offset - detection["offset"]))
    return f"{seconds} s after the first detection"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ---- printable HTML ---------------------------------------------------------------------
_TYPE_LABELS = {"DETECTION": "Detection", "ESCALATION": "Escalation", "VOICE": "Warning spoken", "ALERT": "Owner alert",
                "SIREN": "Siren", "CLEARED": "Cleared", "RECORDING": "Recording", "CLIP_SAVED": "Clip saved",
                "INSIDER": "Insider", "VISITOR": "Visitor", "PANIC": "Panic", "SOUND": "Loud sound",
                "NOTIFICATION": "Notification", "GREETING": "Greeting"}


def render_html(report: dict) -> str:
    e = html.escape
    integrity = report["integrity"]
    badge = {"verified": ("#047857", "Integrity verified"), "tampered": ("#b91c1c", "Integrity check FAILED"),
             "unsealed": ("#a16207", "Not sealed"), "missing": ("#b91c1c", "File missing"),
             "removed": ("#52525b", "Deleted")}.get(integrity.get("status"), ("#52525b", integrity.get("status", "")))
    rows = []
    for ev in report["timeline"]:
        pic = (f'<img src="/api/events/{ev["id"]}/snapshot.jpg?v={e(ev["snapshot"])}" alt="">'
               if ev.get("snapshot") else "")
        offset = f'{ev["offset"]:+.1f}s'
        rows.append(f'<tr><td class="mono">{e(ev["local_time"])}<br><span class="muted">{offset}</span></td>'
                    f'<td><span class="sev sev-{e(ev["severity"].lower())}">{e(_TYPE_LABELS.get(ev["event_type"], ev["event_type"].title()))}</span></td>'
                    f'<td>{e(ev["description"] or "")}</td><td class="pic">{pic}</td></tr>')
    stats = report["stats"]
    facts = [
        ("Camera", report["camera"]),
        ("Type", report["reason_label"]),
        ("Started", datetime.fromisoformat(report["started"]).strftime("%a %d %b %Y, %H:%M:%S %Z").strip()),
        ("Clip length", f'{report["duration"]:.0f} s'),
        ("Peak level", f'{stats["peak_level"]} – {LEVEL_NAMES.get(stats["peak_level"], "none")}' if stats["peak_level"] else "–"),
        ("Warnings spoken", str(stats["warnings"])),
        ("Owner alerted", "Yes" if stats["alerts"] else "No"),
        ("Siren", "Yes" if stats["siren"] else "No"),
    ]
    facts_html = "".join(f"<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>" for k, v in facts)
    sha = integrity.get("sha256") or "–"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Incident report · {e(report["camera"])} · {e(report["started"][:19])}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 14px/1.5 Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #18181b; margin: 0;
         background: #f4f4f5; }}
  main {{ max-width: 900px; margin: 24px auto; background: #fff; padding: 40px; border-radius: 12px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
            border-bottom: 2px solid #18181b; padding-bottom: 16px; }}
  h1 {{ font-size: 22px; margin: 0; }} h2 {{ font-size: 15px; margin: 28px 0 10px; text-transform: uppercase;
  letter-spacing: .06em; color: #52525b; }}
  .brand {{ font-weight: 700; letter-spacing: .08em; color: #2563eb; font-size: 12px; text-transform: uppercase; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; color: #fff; font-weight: 600;
            font-size: 12px; background: {badge[0]}; white-space: nowrap; }}
  .summary {{ font-size: 15px; background: #f8fafc; border-left: 4px solid #2563eb; padding: 14px 16px; margin: 0;
              border-radius: 0 8px 8px 0; }}
  dl {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 0; }}
  dl div {{ border: 1px solid #e4e4e7; border-radius: 8px; padding: 8px 10px; }}
  dt {{ font-size: 11px; color: #71717a; text-transform: uppercase; letter-spacing: .04em; }} dd {{ margin: 2px 0 0;
  font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ border-top: 1px solid #e4e4e7; padding: 8px 6px; vertical-align: top; }}
  .mono, code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }}
  .muted {{ color: #71717a; }} .pic img {{ width: 160px; border-radius: 6px; display: block; }}
  .sev {{ font-size: 11px; font-weight: 600; padding: 2px 6px; border-radius: 4px; background: #f4f4f5; white-space: nowrap; }}
  .sev-low {{ background: #fef9c3; }} .sev-medium {{ background: #fde68a; }} .sev-high {{ background: #fed7aa; }}
  .sev-critical {{ background: #fecaca; }}
  .thumb {{ width: 100%; max-height: 360px; object-fit: cover; border-radius: 8px; margin-top: 16px; }}
  .integrity {{ border: 1px solid #e4e4e7; border-radius: 8px; padding: 12px 14px; }}
  .integrity code {{ word-break: break-all; }}
  footer {{ margin-top: 28px; font-size: 11px; color: #71717a; border-top: 1px solid #e4e4e7; padding-top: 12px; }}
  .toolbar {{ max-width: 900px; margin: 16px auto 0; text-align: right; }}
  .toolbar button {{ font: inherit; padding: 8px 14px; border-radius: 6px; border: 0; background: #2563eb; color: #fff;
                    cursor: pointer; }}
  @media (max-width: 640px) {{ main {{ padding: 20px; margin: 0; border-radius: 0; }} dl {{ grid-template-columns: 1fr 1fr; }}
                               .pic img {{ width: 96px; }} }}
  @media print {{ body {{ background: #fff; }} main {{ box-shadow: none; margin: 0; max-width: none; padding: 0; }}
                  .toolbar {{ display: none; }} tr {{ break-inside: avoid; }} }}
</style></head>
<body>
<div class="toolbar"><button onclick="window.print()">Print / Save as PDF</button></div>
<main>
<header>
  <div><div class="brand">Guardian · Incident report</div><h1>{e(report["reason_label"])} at {e(report["camera"])}</h1>
  <div class="muted">{e(datetime.fromisoformat(report["started"]).strftime("%A %d %B %Y, %H:%M:%S"))}</div></div>
  <span class="badge">{e(badge[1])}</span>
</header>
<h2>Summary</h2>
<p class="summary">{e(report["summary"])}</p>
<img class="thumb" src="/api/recordings/{e(report["file"])}/thumbnail" alt="First frame of the clip" onerror="this.remove()">
<h2>Key facts</h2>
<dl>{facts_html}</dl>
<h2>Timeline</h2>
<table>{"".join(rows) or '<tr><td class="muted">No events were logged for this clip.</td></tr>'}</table>
<h2>Evidence integrity</h2>
<div class="integrity">
  <div><strong>{e(badge[1])}.</strong> {e(integrity.get("detail", ""))}</div>
  <div class="mono" style="margin-top:8px">File: <code>{e(report["file"])}</code></div>
  <div class="mono">SHA-256: <code>{e(sha)}</code></div>
  <div class="mono">Sealed: {e(integrity.get("sealed_at") or "–")} · ledger entry #{e(str(integrity.get("seq") or "–"))}</div>
  <div class="mono">Vault key fingerprint: <code>{e(report["fingerprint"])}</code></div>
</div>
<footer>Generated by Guardian on {e(report["generated"])}. The SHA-256 above can be checked independently with
<code>sha256sum</code> (Linux), <code>shasum -a 256</code> (macOS) or <code>certutil -hashfile &lt;file&gt; SHA256</code> (Windows).</footer>
</main></body></html>"""
