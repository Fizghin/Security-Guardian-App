"""
Outbound alerts: Discord, Telegram, ntfy, a generic webhook and e-mail. All optional.

Settings come from Settings → Notifications (defaults from backend/.env) and are read on
every send, so changes apply at once. Secret URLs and tokens never appear in error
messages, because those end up in the event log.
"""
import base64
import json
import smtplib
import ssl
import threading
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import httpx

from services.event_service import event_service
from services.settings_service import NotificationSettings, settings_service

CHANNELS = ("discord", "telegram", "ntfy", "webhook", "email")
EMAIL_ATTACHMENT_LIMIT = 20 * 1024 * 1024
DISCORD_ATTACHMENT_LIMIT = 8 * 1024 * 1024
TELEGRAM_VIDEO_LIMIT = 50 * 1024 * 1024
NTFY_ATTACHMENT_LIMIT = 15 * 1024 * 1024  # ntfy.sh's limit; self-hosted servers may allow more
TELEGRAM_API = "https://api.telegram.org"
NTFY_PRIORITY = {"CRITICAL": "5", "HIGH": "4", "MEDIUM": "3"}
NTFY_TAGS = {"CRITICAL": "rotating_light", "HIGH": "rotating_light", "MEDIUM": "warning"}

File = tuple[str, bytes, str]  # (name, data, content type)


class ChannelError(RuntimeError):
    pass


def _check(r: httpx.Response, what: str) -> httpx.Response:
    """Raise a readable error that does not contain the (secret) request URL."""
    if r.is_success:
        return r
    detail = ""
    try:
        body = r.json()
        detail = body.get("description") or body.get("error") or body.get("message") or ""
    except ValueError:
        detail = r.text[:200]
    raise ChannelError(f"{what} answered {r.status_code}{f': {detail}' if detail else ''}")


def _post(what: str, method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return _check(httpx.request(method, url, timeout=30, **kwargs), what)
    except httpx.HTTPError as exc:
        raise ChannelError(f"Could not reach {what} ({exc.__class__.__name__})") from None


class NotificationService:
    def __init__(self, settings=settings_service, events=event_service):
        self.settings, self.events = settings, events

    def _cfg(self) -> NotificationSettings:
        return self.settings.get().notifications

    @staticmethod
    def configured(cfg: NotificationSettings) -> list[str]:
        ready = {
            "discord": cfg.discord_webhook.startswith("https://"),
            "telegram": bool(cfg.telegram_token and cfg.telegram_chat_id),
            "ntfy": bool(cfg.ntfy_url),
            "webhook": bool(cfg.webhook_url),
            "email": bool(cfg.smtp_user and cfg.smtp_password and cfg.email_to),
        }
        return [c for c in CHANNELS if ready[c]]

    def status(self) -> dict:
        cfg = self._cfg()
        ready = self.configured(cfg)
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
        _post("Discord", "POST", cfg.discord_webhook, data={"payload_json": json.dumps(payload)},
              files=multipart or None)

    def _telegram(self, cfg, title: str, message: str, files: list[File]) -> None:
        base = f"{TELEGRAM_API}/bot{cfg.telegram_token}"
        text = f"{title}\n{message}"
        image = next((f for f in files if f[2].startswith("image/")), None)
        video = next((f for f in files if f[2].startswith("video/") and len(f[1]) <= TELEGRAM_VIDEO_LIMIT), None)
        if image or video:
            name, data, ctype = image or video
            kind = "photo" if image else "video"
            _post("Telegram", "POST", f"{base}/send{kind.capitalize()}",
                  data={"chat_id": cfg.telegram_chat_id, "caption": text[:1024]}, files={kind: (name, data, ctype)})
        else:
            if files:
                text += "\n\n(The video clip is too large to send; it is saved in Guardian's Recordings.)"
            _post("Telegram", "POST", f"{base}/sendMessage", json={"chat_id": cfg.telegram_chat_id, "text": text})

    def _ntfy(self, cfg, title: str, message: str, severity: str, files: list[File]) -> None:
        # Title and message go in the query string, which carries any characters (headers don't)
        params = {"title": title, "priority": NTFY_PRIORITY.get(severity, "3"), "tags": NTFY_TAGS.get(severity, "bell")}
        headers = {"Authorization": f"Bearer {cfg.ntfy_token}"} if cfg.ntfy_token else {}
        attachment = next((f for f in files if len(f[1]) <= NTFY_ATTACHMENT_LIMIT), None)
        if attachment:
            _post("ntfy", "PUT", cfg.ntfy_url, params={**params, "message": message}, content=attachment[1],
                  headers={**headers, "Filename": attachment[0]})
        else:
            _post("ntfy", "POST", cfg.ntfy_url, params=params, content=message.encode(), headers=headers)

    def _webhook(self, cfg, title: str, message: str, severity: str, files: list[File]) -> None:
        payload = {"title": title, "message": message, "severity": severity,
                   "time": datetime.now().astimezone().isoformat()}
        image = next((f for f in files if f[2].startswith("image/")), None)
        if image:
            payload["snapshot_jpeg_base64"] = base64.b64encode(image[1]).decode()
        clip = next((f for f in files if f[2].startswith("video/")), None)
        if clip:
            payload["clip"] = clip[0]  # the file is in Recordings; it is too large for a JSON body
        _post("The webhook", "POST", cfg.webhook_url, json=payload)

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
        for channel in self.configured(cfg):
            try:
                senders[channel]()
                results.append({"channel": channel, "ok": True, "error": None})
            except Exception as exc:  # each channel fails on its own
                results.append({"channel": channel, "ok": False, "error": str(exc) or exc.__class__.__name__})
        if log:
            for r in results:
                name = r["channel"].capitalize() if r["channel"] != "ntfy" else "ntfy"
                if r["ok"]:
                    self.events.log("NOTIFICATION", f"{name} alert sent: {title}", "INFO")
                else:
                    self.events.log("NOTIFICATION", f"{name} alert failed: {r['error']}", "MEDIUM")
        return results

    # ---- public API -------------------------------------------------------
    def send_alert(self, title: str, message: str, severity: str = "HIGH", snapshot: bytes | None = None) -> bool:
        if not self.configured(self._cfg()):
            return False
        files = [("snapshot.jpg", snapshot, "image/jpeg")] if snapshot else []
        threading.Thread(target=self._send, args=(title, message, severity, files), daemon=True).start()
        return True

    def send_clip(self, title: str, message: str, video_path: str) -> bool:
        if not self.configured(self._cfg()):
            return False
        path = Path(video_path)
        files = [(path.name, path.read_bytes(), "video/mp4")] if path.is_file() else []
        threading.Thread(target=self._send, args=(title, message, "HIGH", files), daemon=True).start()
        return True

    def send_test(self) -> list[dict]:
        return self._send("Test notification", "This is a test alert from Guardian. Notifications are working.",
                          "INFO", [], log=False)

    def telegram_chats(self, token: str = "") -> list[dict]:
        """Chats that recently messaged the bot, so the user can pick one instead of looking up an id."""
        token = token.strip() or self._cfg().telegram_token
        if not token:
            raise ChannelError("Enter the bot token first")
        updates = _post("Telegram", "GET", f"{TELEGRAM_API}/bot{token}/getUpdates").json().get("result", [])
        chats = {}
        for update in updates:
            for key in ("message", "channel_post", "my_chat_member", "edited_message"):
                chat = (update.get(key) or {}).get("chat")
                if chat:
                    name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
                    chats[chat["id"]] = {"id": str(chat["id"]), "name": name or chat.get("username") or str(chat["id"]),
                                         "type": chat.get("type", "")}
        return list(chats.values())


notification_service = NotificationService()
