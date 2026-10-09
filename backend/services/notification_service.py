"""
Outbound alerts: Discord, Telegram, ntfy, a generic webhook and e-mail. All optional.

Settings come from Settings → Notifications (defaults from backend/.env) and are read on
every send, so changes apply at once. Secret URLs and tokens never appear in error
messages, because those end up in the event log: servers' replies are scrubbed of them,
and HTML error pages are left out.
"""
import base64
import json
import re
import smtplib
import ssl
import threading
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import cv2
import httpx
import numpy as np

from services.event_service import event_service
from services.settings_service import CHANNELS, URL_FIELDS, NotificationSettings, settings_service

EMAIL_ATTACHMENT_LIMIT = 20 * 1024 * 1024
DISCORD_ATTACHMENT_LIMIT = 8 * 1024 * 1024
TELEGRAM_VIDEO_LIMIT = 50 * 1024 * 1024
NTFY_ATTACHMENT_LIMIT = 15 * 1024 * 1024  # ntfy.sh's limit; self-hosted servers may allow more
TELEGRAM_API = "https://api.telegram.org"
NTFY_PRIORITY = {"CRITICAL": "5", "HIGH": "4", "MEDIUM": "3"}
NTFY_TAGS = {"CRITICAL": "rotating_light", "HIGH": "rotating_light", "MEDIUM": "warning"}

File = tuple[str, bytes, str]  # (name, data, content type)


class ChannelError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status  # the server's HTTP status, if it answered


def _secrets(cfg: NotificationSettings) -> list[str]:
    """Every part of the configured addresses and tokens that an error message must not repeat."""
    parts = {cfg.telegram_token, cfg.ntfy_token, cfg.smtp_password}
    for url in (getattr(cfg, field) for field in URL_FIELDS):
        if not url:
            continue
        split = urlsplit(url)
        parts |= {url, url.split("://", 1)[-1], split.query, *(v for _, v in parse_qsl(split.query))}
        # Servers often quote only the end of the path ("webhook/<id> is not registered")
        segments = split.path.strip("/").split("/")
        for i in range(len(segments)):
            tail = "/".join(segments[i:])
            parts |= {tail, unquote(tail)}
    return sorted((p for p in parts if len(p) >= 3), key=len, reverse=True)


def _scrub(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "…")
    return text


def _check(r: httpx.Response, what: str, secrets: list[str]) -> httpx.Response:
    """Raise a readable error with the server's reason, minus secrets and HTML."""
    if r.is_success:
        return r
    detail = ""
    try:
        body = r.json()
        if isinstance(body, dict):
            keys = ("description", "error", "message", "detail")
            detail = next((body[k] for k in keys if isinstance(body.get(k), str) and body[k]), "")
    except ValueError:
        if "html" not in r.headers.get("content-type", "") and not re.search(r"<[!/a-zA-Z][^>]*>", r.text):
            detail = r.text
    # Scrub before shortening, so a cut can't leave the start of a secret behind
    detail = _scrub(" ".join(detail.split()), secrets)[:200] or r.reason_phrase
    raise ChannelError(f"{what} answered {r.status_code}{f': {detail}' if detail else ''}", r.status_code)


def _post(cfg: NotificationSettings, what: str, method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return _check(httpx.request(method, url, timeout=30, **kwargs), what, _secrets(cfg))
    except httpx.HTTPError as exc:
        raise ChannelError(f"Could not reach {what} ({exc.__class__.__name__})") from None


def _test_picture() -> bytes:
    """A small picture for Send test, so each channel is tested the way real alerts are sent."""
    img = np.full((120, 320, 3), 40, np.uint8)
    cv2.putText(img, "Guardian test", (24, 72), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


class NotificationService:
    def __init__(self, settings=settings_service, events=event_service):
        self.settings, self.events = settings, events

    def _cfg(self) -> NotificationSettings:
        return self.settings.get().notifications

    def status(self) -> dict:
        cfg = self._cfg()
        ready = cfg.configured()
        return {**{c: c in ready for c in CHANNELS}, "any": bool(ready),
                "email_to": cfg.email_to if "email" in ready else None}

    # ---- channels -------------------------------------------------------
    def _discord(self, cfg, title: str, message: str, severity: str, files: list[File]) -> None:
        files = [f for f in files if len(f[1]) <= DISCORD_ATTACHMENT_LIMIT]
        color = {"CRITICAL": 0xDC2626, "HIGH": 0xEA580C, "MEDIUM": 0xD97706}.get(severity, 0x2563EB)
        payload = {"embeds": [{"title": title, "description": message, "color": color,
                               "timestamp": datetime.now().astimezone().isoformat()}]}
        if files and files[0][2].startswith("image/"):
            payload["embeds"][0]["image"] = {"url": f"attachment://{files[0][0]}"}
        multipart = {f"files[{i}]": (name, data, ctype) for i, (name, data, ctype) in enumerate(files)}
        _post(cfg, "Discord", "POST", cfg.discord_webhook, data={"payload_json": json.dumps(payload)},
              files=multipart or None)

    def _telegram(self, cfg, title: str, message: str, files: list[File]) -> None:
        base = f"{TELEGRAM_API}/bot{cfg.telegram_token}"
        text = f"{title}\n{message}"
        image = next((f for f in files if f[2].startswith("image/")), None)
        video = next((f for f in files if f[2].startswith("video/") and len(f[1]) <= TELEGRAM_VIDEO_LIMIT), None)
        if image or video:
            name, data, ctype = image or video
            kind = "photo" if image else "video"
            _post(cfg, "Telegram", "POST", f"{base}/send{kind.capitalize()}",
                  data={"chat_id": cfg.telegram_chat_id, "caption": text[:1024]}, files={kind: (name, data, ctype)})
        else:
            if files:
                text += "\n\n(The video clip is too large to send; it is saved in Guardian's Recordings.)"
            _post(cfg, "Telegram", "POST", f"{base}/sendMessage", json={"chat_id": cfg.telegram_chat_id, "text": text})

    def _ntfy(self, cfg, title: str, message: str, severity: str, files: list[File]) -> str | None:
        # Title and message go in the query string, which carries any characters (headers don't)
        params = {"title": title, "priority": NTFY_PRIORITY.get(severity, "3"), "tags": NTFY_TAGS.get(severity, "bell")}
        headers = {"Authorization": f"Bearer {cfg.ntfy_token}"} if cfg.ntfy_token else {}
        attachment = next((f for f in files if len(f[1]) <= NTFY_ATTACHMENT_LIMIT), None)
        if attachment:
            try:
                _post(cfg, "ntfy", "PUT", cfg.ntfy_url, params={**params, "message": message}, content=attachment[1],
                      headers={**headers, "Filename": attachment[0]})
                return None
            except ChannelError as exc:
                # Servers without attachments turned on (the default when self-hosted) refuse the file
                # with a 4xx; the alert itself still matters, so send it as text.
                if not (exc.status and 400 <= exc.status < 500):
                    raise
                what = "picture" if attachment[2].startswith("image/") else "video clip"
                _post(cfg, "ntfy", "POST", cfg.ntfy_url, params=params, headers=headers,
                      content=f"{message}\n\n(The {what} could not be attached.)".encode())
                return f"The {what} was left out ({exc})"
        _post(cfg, "ntfy", "POST", cfg.ntfy_url, params=params, content=message.encode(), headers=headers)
        return None

    def _webhook(self, cfg, title: str, message: str, severity: str, files: list[File]) -> None:
        payload = {"title": title, "message": message, "severity": severity,
                   "time": datetime.now().astimezone().isoformat()}
        image = next((f for f in files if f[2].startswith("image/")), None)
        if image:
            payload["snapshot_jpeg_base64"] = base64.b64encode(image[1]).decode()
        clip = next((f for f in files if f[2].startswith("video/")), None)
        if clip:
            payload["clip"] = clip[0]  # the file is in Recordings; it is too large for a JSON body
        _post(cfg, "The webhook", "POST", cfg.webhook_url, json=payload)

    def _email(self, cfg, subject: str, message: str, files: list[File]) -> None:
        sendable = [f for f in files if len(f[1]) <= EMAIL_ATTACHMENT_LIMIT]
        if len(sendable) < len(files):
            message += "\n\n(The video clip was too large to attach; it is saved in Guardian's Recordings.)"
        msg = EmailMessage()
        msg["From"] = cfg.smtp_user
        msg["To"] = cfg.email_to
        msg["Subject"] = f"[Guardian] {subject}"
        msg.set_content(f"{message}\n\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nSent by Guardian.")
        for name, data, ctype in sendable:
            maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        context = ssl.create_default_context()
        if cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=30) as server:
                server.login(cfg.smtp_user, cfg.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
                server.starttls(context=context)
                server.login(cfg.smtp_user, cfg.smtp_password)
                server.send_message(msg)

    def _send(self, title: str, message: str, severity: str, files: list[File], log: bool = True) -> list[dict]:
        cfg = self._cfg()
        senders = {
            "discord": lambda: self._discord(cfg, title, message, severity, files),
            "telegram": lambda: self._telegram(cfg, title, message, files),
            "ntfy": lambda: self._ntfy(cfg, title, message, severity, files),
            "webhook": lambda: self._webhook(cfg, title, message, severity, files),
            "email": lambda: self._email(cfg, title, message, files),
        }
        results = []
        for channel in cfg.configured():
            try:
                # A sender returns a note when the alert went out without part of it
                results.append({"channel": channel, "ok": True, "error": None, "note": senders[channel]()})
            except ChannelError as exc:  # each channel fails on its own
                results.append({"channel": channel, "ok": False, "error": str(exc), "note": None})
            except Exception as exc:  # e.g. the mail server's reply, which may quote anything
                error = _scrub(str(exc), _secrets(cfg)) or exc.__class__.__name__
                results.append({"channel": channel, "ok": False, "error": error, "note": None})
        if log:
            for r in results:
                name = r["channel"].capitalize() if r["channel"] != "ntfy" else "ntfy"
                if not r["ok"]:
                    self.events.log("NOTIFICATION", f"{name} alert failed: {r['error']}", "MEDIUM")
                elif r["note"]:
                    self.events.log("NOTIFICATION", f"{name} alert sent: {title}. {r['note']}", "LOW")
                else:
                    self.events.log("NOTIFICATION", f"{name} alert sent: {title}", "INFO")
        return results

    # ---- public API -------------------------------------------------------
    def send_alert(self, title: str, message: str, severity: str = "HIGH", snapshot: bytes | None = None) -> bool:
        if not self._cfg().configured():
            return False
        files = [("snapshot.jpg", snapshot, "image/jpeg")] if snapshot else []
        threading.Thread(target=self._send, args=(title, message, severity, files), daemon=True).start()
        return True

    def send_clip(self, title: str, message: str, video_path: str) -> bool:
        if not self._cfg().configured():
            return False
        path = Path(video_path)
        files = [(path.name, path.read_bytes(), "video/mp4")] if path.is_file() else []
        threading.Thread(target=self._send, args=(title, message, "HIGH", files), daemon=True).start()
        return True

    def send_test(self) -> list[dict]:
        return self._send("Test notification", "This is a test alert from Guardian. Notifications are working.",
                          "INFO", [("test.jpg", _test_picture(), "image/jpeg")], log=False)

    def telegram_chats(self, token: str = "") -> list[dict]:
        """Chats that recently messaged the bot, so the user can pick one instead of looking up an id."""
        cfg = self._cfg()
        if token.strip():
            cfg = cfg.model_copy(update={"telegram_token": token.strip()})
        if not cfg.telegram_token:
            raise ChannelError("Enter the bot token first")
        reply = _post(cfg, "Telegram", "GET", f"{TELEGRAM_API}/bot{cfg.telegram_token}/getUpdates").json()
        chats = {}
        for update in reply.get("result", []):
            for key in ("message", "channel_post", "my_chat_member", "edited_message"):
                chat = (update.get(key) or {}).get("chat")
                if chat:
                    name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
                    chats[chat["id"]] = {"id": str(chat["id"]), "name": name or chat.get("username") or str(chat["id"]),
                                         "type": chat.get("type", "")}
        return list(chats.values())


notification_service = NotificationService()
