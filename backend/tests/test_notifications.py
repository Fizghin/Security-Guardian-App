import base64
import json

import httpx
import pytest

import api as api_module
from services import notification_service as module
from services.notification_service import NotificationService
from services.settings_service import SettingsService, settings_service

DISCORD = "https://discord.com/api/webhooks/123/SECRET-DISCORD"
TOKEN = "123456:SECRET-TELEGRAM"


class Events:
    def __init__(self):
        self.logged = []

    def log(self, event_type, description, severity="INFO", **kwargs):
        self.logged.append((event_type, description, severity))


@pytest.fixture
def svc(tmp_path):
    return SettingsService(tmp_path / "settings.json")


class Calls(list):
    replies: dict


@pytest.fixture
def calls(monkeypatch):
    seen, replies = Calls(), {}

    def request(method, url, **kwargs):
        seen.append({"method": method, "url": url, **kwargs})
        status, body = next((v for k, v in replies.items() if k in url), (200, {"ok": True}))
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(module.httpx, "request", request)
    seen.replies = replies
    return seen


def notifier(svc, **cfg):
    svc.update({"notifications": cfg})
    return NotificationService(settings=svc, events=Events())


def test_nothing_configured_sends_nothing(svc, calls):
    n = notifier(svc)
    assert n.status()["any"] is False
    assert n.send_alert("t", "m") is False
    assert calls == []


def test_telegram_sends_the_picture_with_a_caption(svc, calls):
    n = notifier(svc, telegram_token=TOKEN, telegram_chat_id="42")
    assert n._send("Intruder at Door", "Someone is there", "HIGH", [("snapshot.jpg", b"jpeg", "image/jpeg")])[0]["ok"]
    call = calls[0]
    assert call["url"] == f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    assert call["data"] == {"chat_id": "42", "caption": "Intruder at Door\nSomeone is there"}
    assert call["files"]["photo"] == ("snapshot.jpg", b"jpeg", "image/jpeg")
    n._send("Test", "text only", "INFO", [])
    assert calls[1]["url"].endswith("/sendMessage") and calls[1]["json"]["text"] == "Test\ntext only"


def test_ntfy_attaches_the_picture_and_maps_priority(svc, calls):
    n = notifier(svc, ntfy_url="https://ntfy.sh/my-topic", ntfy_token="tk")
    n._send("Alarm at Door", "Siren sounding · now", "CRITICAL", [("snapshot.jpg", b"jpeg", "image/jpeg")])
    call = calls[0]
    assert (call["method"], call["url"], call["content"]) == ("PUT", "https://ntfy.sh/my-topic", b"jpeg")
    assert call["params"] == {"title": "Alarm at Door", "message": "Siren sounding · now", "priority": "5",
                              "tags": "rotating_light"}
    assert call["headers"] == {"Authorization": "Bearer tk", "Filename": "snapshot.jpg"}
    n._send("Test", "plain", "INFO", [])
    assert calls[1]["method"] == "POST" and calls[1]["content"] == b"plain" and calls[1]["params"]["priority"] == "3"


def test_webhook_posts_json_with_the_picture(svc, calls):
    n = notifier(svc, webhook_url="http://192.168.1.5:8123/api/webhook/guardian")
    n._send("Intruder", "msg", "HIGH", [("snapshot.jpg", b"jpeg", "image/jpeg")])
    body = calls[0]["json"]
    assert body["title"] == "Intruder" and body["severity"] == "HIGH" and body["time"]
    assert base64.b64decode(body["snapshot_jpeg_base64"]) == b"jpeg"


def test_discord_embeds_the_picture(svc, calls):
    n = notifier(svc, discord_webhook=DISCORD)
    n._send("Intruder", "msg", "HIGH", [("snapshot.jpg", b"jpeg", "image/jpeg")])
    payload = json.loads(calls[0]["data"]["payload_json"])
    assert payload["embeds"][0]["image"]["url"] == "attachment://snapshot.jpg"


def test_email_is_sent(svc, monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, host, port, timeout=None):
            sent.append((host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            sent.append((user, password))

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(module.smtplib, "SMTP", SMTP)
    n = notifier(svc, smtp_user="me@example.com", smtp_password="pw", email_to="you@example.com")
    assert n._send("Intruder", "msg", "HIGH", [("snapshot.jpg", b"jpeg", "image/jpeg")])[0]["ok"]
    msg = sent[-1]
    assert msg["To"] == "you@example.com" and msg["Subject"] == "[Guardian] Intruder"
    assert sent[0] == ("smtp.gmail.com", 587) and sent[1] == ("me@example.com", "pw")


def test_errors_never_contain_secrets_and_channels_fail_on_their_own(svc, calls):
    calls.replies["api.telegram.org"] = (401, {"ok": False, "description": "Unauthorized"})
    calls.replies["discord.com"] = (404, {"message": "Unknown Webhook"})
    n = notifier(svc, discord_webhook=DISCORD, telegram_token=TOKEN, telegram_chat_id="42",
                 ntfy_url="https://ntfy.sh/topic")
    results = {r["channel"]: r for r in n._send("t", "m", "HIGH", [])}
    assert results["telegram"]["error"] == "Telegram answered 401: Unauthorized"
    assert results["discord"]["error"] == "Discord answered 404: Unknown Webhook"
    assert results["ntfy"]["ok"], "one broken channel must not stop the others"
    logged = " ".join(d for _, d, _ in n.events.logged)
    assert "SECRET" not in logged and "SECRET" not in json.dumps(results)


def test_unreachable_server_error_has_no_url(svc, monkeypatch):
    def fail(method, url, **kwargs):
        raise httpx.ConnectError(f"cannot connect to {url}")

    monkeypatch.setattr(module.httpx, "request", fail)
    n = notifier(svc, discord_webhook=DISCORD)
    error = n._send("t", "m", "HIGH", [])[0]["error"]
    assert error == "Could not reach Discord (ConnectError)"


def test_telegram_chat_finder(svc, calls):
    calls.replies["getUpdates"] = (200, {"ok": True, "result": [
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Sam", "last_name": "Lee"}}},
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Sam", "last_name": "Lee"}}},
        {"channel_post": {"chat": {"id": -100, "type": "channel", "title": "Home alerts"}}},
    ]})
    n = notifier(svc, telegram_token=TOKEN)
    assert n.telegram_chats() == [{"id": "42", "name": "Sam Lee", "type": "private"},
                                  {"id": "-100", "name": "Home alerts", "type": "channel"}]
    assert calls[0]["url"] == f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    with pytest.raises(module.ChannelError, match="token"):
        notifier(svc, telegram_token="").telegram_chats()


def test_env_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/topic")
    monkeypatch.setenv("SMTP_PORT", "465")
    cfg = SettingsService(tmp_path / "settings.json").get().notifications
    assert (cfg.telegram_token, cfg.telegram_chat_id, cfg.ntfy_url, cfg.smtp_port) == (TOKEN, "42", "https://ntfy.sh/topic", 465)


def test_secrets_are_masked_kept_and_cleared(svc):
    svc.update({"notifications": {"telegram_token": TOKEN, "telegram_chat_id": "42", "webhook_url": "https://x.example/h"}})
    public = svc.public()["notifications"]
    assert "telegram_token" not in public and public["telegram_token_set"] is True
    assert "webhook_url" not in public and public["webhook_url_set"] is True
    assert public["telegram_chat_id"] == "42", "ids are not secret"
    assert TOKEN not in json.dumps(svc.public())
    svc.update({"notifications": {"telegram_chat_id": "43"}})  # a save that leaves the token out keeps it
    assert svc.get().notifications.telegram_token == TOKEN
    svc.update({"notifications": {"telegram_token": ""}})
    assert svc.get().notifications.telegram_token == ""


def test_bad_urls_are_rejected(svc):
    from services.settings_service import SettingsError
    with pytest.raises(SettingsError):
        svc.update({"notifications": {"ntfy_url": "ntfy.sh/topic"}})


def test_api_drops_set_flags(monkeypatch):
    before = settings_service.get().notifications.model_dump()
    try:
        result = api_module.patch_settings({"notifications": {"telegram_token_set": False, "telegram_chat_id": "7"}})
        assert result["notifications"]["telegram_chat_id"] == "7"
    finally:
        settings_service.update({"notifications": before})
