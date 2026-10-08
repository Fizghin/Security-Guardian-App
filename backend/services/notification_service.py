"""
Outbound alerts: Discord webhook and/or e-mail (SMTP). Both are optional and
configured through .env so credentials never pass through the dashboard.
"""
import json
import smtplib
import ssl
import threading
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import httpx

from config import env_str
from services.event_service import event_service

EMAIL_ATTACHMENT_LIMIT = 20 * 1024 * 1024
DISCORD_ATTACHMENT_LIMIT = 8 * 1024 * 1024


class NotificationService:
    def __init__(self):
        self.discord_webhook = env_str("DISCORD_WEBHOOK_URL") or env_str("DISCORD_WEBHOOK")
        self.smtp_host = env_str("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(env_str("SMTP_PORT", "587"))
        self.smtp_user = env_str("SMTP_USER")
        self.smtp_password = env_str("SMTP_PASSWORD")
        self.alert_email = env_str("ALERT_EMAIL")

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.alert_email)

    @property
    def discord_enabled(self) -> bool:
        return self.discord_webhook.startswith("https://")

    def status(self) -> dict:
        return {
            "discord": self.discord_enabled,
            "email": self.email_enabled,
            "email_to": self.alert_email if self.email_enabled else None,
        }

    # ---- channels -------------------------------------------------------
    def _discord(self, title: str, message: str, severity: str, files: list[tuple[str, bytes, str]]) -> None:
        color = {"CRITICAL": 0xDC2626, "HIGH": 0xEA580C, "MEDIUM": 0xD97706}.get(severity, 0x2563EB)
        payload = {"embeds": [{"title": title, "description": message, "color": color,
                               "timestamp": datetime.now().astimezone().isoformat()}]}
        if files and files[0][2].startswith("image/"):
            payload["embeds"][0]["image"] = {"url": f"attachment://{files[0][0]}"}
        multipart = {f"files[{i}]": (name, data, ctype) for i, (name, data, ctype) in enumerate(files)}
        r = httpx.post(self.discord_webhook, data={"payload_json": json.dumps(payload)},
                       files=multipart or None, timeout=30)
        r.raise_for_status()

    def _email(self, subject: str, message: str, files: list[tuple[str, bytes, str]]) -> None:
        msg = EmailMessage()
        msg["From"] = self.smtp_user
        msg["To"] = self.alert_email
        msg["Subject"] = f"[Guardian] {subject}"
        msg.set_content(f"{message}\n\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nSent by Guardian.")
        for name, data, ctype in files:
            maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        context = ssl.create_default_context()
        if self.smtp_port == 465:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context, timeout=30) as server:
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.starttls(context=context)
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

    def _send(self, title, message, severity, files, log=True) -> list[dict]:
        results = []
        if self.discord_enabled:
            discord_files = [f for f in files if len(f[1]) <= DISCORD_ATTACHMENT_LIMIT]
            try:
                self._discord(title, message, severity, discord_files)
                results.append({"channel": "discord", "ok": True, "error": None})
            except Exception as exc:
                results.append({"channel": "discord", "ok": False, "error": str(exc)})
        if self.email_enabled:
            email_files = [f for f in files if len(f[1]) <= EMAIL_ATTACHMENT_LIMIT]
            body = message
            if len(email_files) < len(files):
                body += "\n\n(The video clip was too large to attach; it is saved in Guardian's Recordings.)"
            try:
                self._email(title, body, email_files)
                results.append({"channel": "email", "ok": True, "error": None})
            except Exception as exc:
                results.append({"channel": "email", "ok": False, "error": str(exc)})
        if log:
            for r in results:
                if r["ok"]:
                    event_service.log("NOTIFICATION", f"{r['channel'].capitalize()} alert sent: {title}", "INFO")
                else:
                    event_service.log("NOTIFICATION", f"{r['channel'].capitalize()} alert failed: {r['error']}", "MEDIUM")
        return results

    # ---- public API -------------------------------------------------------
    def send_alert(self, title: str, message: str, severity: str = "HIGH", snapshot: bytes | None = None) -> bool:
        if not (self.discord_enabled or self.email_enabled):
            return False
        files = [("snapshot.jpg", snapshot, "image/jpeg")] if snapshot else []
        threading.Thread(target=self._send, args=(title, message, severity, files), daemon=True).start()
        return True

    def send_clip(self, title: str, message: str, video_path: str) -> bool:
        if not (self.discord_enabled or self.email_enabled):
            return False
        path = Path(video_path)
        files = []
        if path.is_file():
            files.append((path.name, path.read_bytes(), "video/mp4"))
        threading.Thread(target=self._send, args=(title, message, "HIGH", files), daemon=True).start()
        return True

    def send_test(self) -> list[dict]:
        return self._send("Test notification", "This is a test alert from Guardian. Notifications are working.",
                          "INFO", [], log=False)


notification_service = NotificationService()
