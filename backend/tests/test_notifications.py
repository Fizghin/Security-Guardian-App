import base64
import json

import httpx
import pytest

import api as api_module
from services import notification_service as module
from services.notification_service import NotificationService
from services.settings_service import SettingsError, SettingsService, settings_service

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
    with pytest.raises(SettingsError, match="ntfy_url: must be a web address starting with https:// or http://"):
        svc.update({"notifications": {"ntfy_url": "ntfy.sh/topic"}})
    with pytest.raises(SettingsError, match="discord_webhook: must be a web address starting with https://$"):
        svc.update({"notifications": {"discord_webhook": "http://127.0.0.1:8198/discord/X"}})
    with pytest.raises(SettingsError, match="webhook_url"):
        svc.update({"notifications": {"webhook_url": "https://"}})
    svc.update({"notifications": {"webhook_url": "http://192.168.1.5:8123/hook"}})  # local http is fine here
    assert svc.get().notifications.configured() == ["webhook"]


@pytest.mark.parametrize("name, value", [("NTFY_URL", "ntfy.sh/my-topic"), ("WEBHOOK_URL", "example.com/hook"),
                                         ("DISCORD_WEBHOOK_URL", "discord.com/api/webhooks/1/abc"),
                                         ("DISCORD_WEBHOOK_URL", "http://discord.com/api/webhooks/1/abc")])
def test_a_bad_address_in_env_is_ignored_with_a_warning(tmp_path, monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ai": {"humor": 70}}))
    svc = SettingsService(path)  # used to raise and stop Guardian from starting
    out = capsys.readouterr().out
    assert f"Ignoring {name} in backend/.env" in out and "settings.json" not in out
    assert value not in out, "the address may be secret"
    cfg = svc.get().notifications
    assert (cfg.ntfy_url, cfg.webhook_url, cfg.discord_webhook) == ("", "", "")
    assert cfg.telegram_token == TOKEN and svc.get().ai.humor == 70, "everything else still applies"
    svc.update({"ai": {"humor": 10}})
    assert "Ignoring" not in capsys.readouterr().out, "warned once, not on every save"


def test_an_old_http_discord_address_in_settings_json_is_dropped_alone(tmp_path, capsys):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ai": {"humor": 70}, "notifications": {
        "discord_webhook": "http://127.0.0.1:8198/discord/X", "telegram_token": TOKEN, "telegram_chat_id": "42"}}))
    s = SettingsService(path).get()
    assert "Ignoring notifications.discord_webhook in settings.json" in capsys.readouterr().out
    assert s.notifications.discord_webhook == "" and s.ai.humor == 70
    assert s.notifications.configured() == ["telegram"]


def test_one_rule_for_configured(svc):
    n = notifier(svc, discord_webhook=DISCORD, telegram_token=TOKEN, smtp_user="me@example.com", email_to="x@example.com")
    public = svc.public()["notifications"]
    assert public["configured"] == ["discord"], "telegram needs a chat and e-mail a password"
    assert n.status() == {"discord": True, "telegram": False, "ntfy": False, "webhook": False, "email": False,
                          "any": True, "email_to": None}


def test_ntfy_resends_as_text_when_the_server_refuses_the_picture(svc, monkeypatch):
    seen = []

    def request(method, url, **kwargs):
        seen.append((method, kwargs))
        if method == "PUT":  # a self-hosted ntfy without attachment-cache-dir
            body = {"code": 40014, "http": 400, "error": "invalid request: attachments not allowed"}
            return httpx.Response(400, json=body, request=httpx.Request(method, url))
        return httpx.Response(200, json={"id": "x"}, request=httpx.Request(method, url))

    monkeypatch.setattr(module.httpx, "request", request)
    n = notifier(svc, ntfy_url="http://127.0.0.1:18090/guardian-selfhosted")
    [result] = n._send("Alarm raised", "Panic button", "CRITICAL", [("snapshot.jpg", b"jpeg", "image/jpeg")])
    assert result["ok"] and result["note"] == "The picture was left out (ntfy answered 400: invalid request: attachments not allowed)"
    assert [m for m, _ in seen] == ["PUT", "POST"]
    assert seen[1][1]["content"] == b"Panic button\n\n(The picture could not be attached.)"
    assert seen[1][1]["params"]["title"] == "Alarm raised" and seen[1][1]["params"]["priority"] == "5"
    assert n.events.logged == [("NOTIFICATION", "ntfy alert sent: Alarm raised. The picture was left out "
                                                "(ntfy answered 400: invalid request: attachments not allowed)", "LOW")]
    # Send test takes the same path, so it can't report an all-clear that real alerts won't get
    [result] = n.send_test()
    assert result["ok"] and result["note"].startswith("The picture was left out")
    assert seen[2][0] == "PUT" and seen[2][1]["content"][:2] == b"\xff\xd8", "a real JPEG is attached"


def test_ntfy_does_not_resend_when_the_server_is_down(svc, monkeypatch):
    seen = []

    def request(method, url, **kwargs):
        seen.append(method)
        return httpx.Response(502, text="Bad Gateway", request=httpx.Request(method, url))

    monkeypatch.setattr(module.httpx, "request", request)
    n = notifier(svc, ntfy_url="https://ntfy.sh/topic")
    [result] = n._send("t", "m", "HIGH", [("snapshot.jpg", b"jpeg", "image/jpeg")])
    assert not result["ok"] and result["error"] == "ntfy answered 502: Bad Gateway" and seen == ["PUT"]


def test_send_test_attaches_a_picture_everywhere(svc, calls):
    n = notifier(svc, discord_webhook=DISCORD, telegram_token=TOKEN, telegram_chat_id="42",
                 webhook_url="https://hooks.example.com/h")
    assert all(r["ok"] for r in n.send_test())
    by_url = {c["url"]: c for c in calls}
    assert by_url[f"https://api.telegram.org/bot{TOKEN}/sendPhoto"]["files"]["photo"][0] == "test.jpg"
    assert json.loads(by_url[DISCORD]["data"]["payload_json"])["embeds"][0]["image"]["url"] == "attachment://test.jpg"
    assert base64.b64decode(by_url["https://hooks.example.com/h"]["json"]["snapshot_jpeg_base64"])[:2] == b"\xff\xd8"
    assert n.events.logged == [], "tests are not logged"


@pytest.mark.parametrize("url, status, reply, kwargs", [
    # n8n quotes the end of the path
    ("http://127.0.0.1:8198/n8n404/webhook/5f1c2b-SECRET-UUID", 404, None,
     {"json": {"message": 'The requested webhook "POST webhook/5f1c2b-SECRET-UUID" is not registered.'}}),
    ("http://127.0.0.1:8198/json500/webhook/5f1c2b-SECRET-UUID", 500, None,
     {"json": {"message": "Webhook /json500/webhook/5f1c2b-SECRET-UUID not registered"}}),
    # Express / Node-RED answer with an HTML page that quotes the whole path
    ("http://127.0.0.1:8198/express404/WEBHOOKSECRET2", 404, "The webhook answered 404: Not Found",
     {"text": '<!DOCTYPE html><html><body><pre>Cannot POST /express404/WEBHOOKSECRET2</pre></body></html>',
      "headers": {"content-type": "text/html; charset=utf-8"}}),
    ("http://127.0.0.1:8198/plain/SECRETQUERY?key=SECRET-KEY-VALUE", 403, None,
     {"text": "Forbidden: bad key SECRET-KEY-VALUE for http://127.0.0.1:8198/plain/SECRETQUERY?key=SECRET-KEY-VALUE"}),
])
def test_server_replies_are_scrubbed_of_secret_addresses(svc, monkeypatch, url, status, reply, kwargs):
    monkeypatch.setattr(module.httpx, "request",
                        lambda method, u, **kw: httpx.Response(status, request=httpx.Request(method, u), **kwargs))
    n = notifier(svc, webhook_url=url)
    error = n.send_test()[0]["error"]
    assert error.startswith(f"The webhook answered {status}")
    if reply:
        assert error == reply
    n._send("Intruder", "msg", "HIGH", [])
    for text in [error, *(d for _, d, _ in n.events.logged)]:
        assert "SECRET" not in text and "<" not in text, text


def test_other_errors_are_scrubbed_too(svc, monkeypatch):
    def fail(method, url, **kwargs):
        raise ValueError(f"bad request line for {url} with Bearer tk_SECRETNTFY")

    monkeypatch.setattr(module.httpx, "request", fail)
    n = notifier(svc, ntfy_url="https://ntfy.example.com/SECRET-TOPIC", ntfy_token="tk_SECRETNTFY")
    error = n._send("t", "m", "HIGH", [])[0]["error"]
    assert "SECRET" not in error and error.startswith("bad request line for …")


def test_api_drops_set_flags(monkeypatch):
    before = settings_service.get().notifications.model_dump()
    try:
        result = api_module.patch_settings({"notifications": {"telegram_token_set": False, "telegram_chat_id": "7",
                                                               "configured": ["telegram"]}})
        assert result["notifications"]["telegram_chat_id"] == "7"
        assert result["notifications"]["configured"] == []
    finally:
        settings_service.update({"notifications": before})


def test_api_ignores_a_secret_of_only_spaces():
    before = settings_service.get().notifications.model_dump()
    try:
        api_module.patch_settings({"notifications": {"discord_webhook": DISCORD}})
        result = api_module.patch_settings({"notifications": {"discord_webhook": "   ", "telegram_chat_id": "8"}})
        assert result["notifications"]["discord_webhook_set"] is True, "a stray space must not delete the secret"
        assert result["notifications"]["telegram_chat_id"] == "8"
        result = api_module.patch_settings({"notifications": {"discord_webhook": ""}})
        assert result["notifications"]["discord_webhook_set"] is False, "an empty value still removes it"
    finally:
        settings_service.update({"notifications": before})


def test_telegram_chat_lookup_takes_the_token_in_a_post_body(calls):
    from fastapi.testclient import TestClient
    from main import app

    calls.replies["getUpdates"] = (200, {"ok": True, "result": [
        {"message": {"chat": {"id": 7, "type": "private", "first_name": "Sam"}}}]})
    client = TestClient(app)
    r = client.post("/api/notifications/telegram/chats", json={"token": TOKEN})
    assert r.status_code == 200 and r.json() == {"chats": [{"id": "7", "name": "Sam", "type": "private"}]}
    assert calls[0]["url"] == f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    # GET is gone (405, or 404 once the dashboard's build is served), so a token can't go in a URL
    assert client.get("/api/notifications/telegram/chats", params={"token": TOKEN}).status_code in (404, 405)
    assert len(calls) == 1
    calls.replies["getUpdates"] = (401, {"ok": False, "description": "Unauthorized"})
    r = client.post("/api/notifications/telegram/chats", json={"token": TOKEN})
    assert r.status_code == 502 and r.json()["detail"] == "Telegram answered 401: Unauthorized"
