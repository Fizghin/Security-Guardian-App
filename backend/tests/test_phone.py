import asyncio
import time

import pytest
from cryptography import x509

from main import PhonePortGuard
from services import phone_service
from services.phone_service import PhoneHub, ensure_certificate, lan_addresses


def test_hub_authenticates_and_queues_commands():
    hub = PhoneHub()
    source = hub.register("door", "secret-token")
    assert hub.authenticate("wrong") is None and hub.authenticate("") is None
    link = hub.authenticate("secret-token")
    assert link.camera_id == "door" and link.source is source

    assert hub.send("door", {"type": "speak", "text": "Hi"}) is False, "nothing is queued for an offline phone"
    source.touch({"battery": 80})
    assert hub.send("door", {"type": "speak", "text": "Hi"}) is True
    assert hub.take_commands(link) == [{"type": "speak", "text": "Hi"}]
    assert hub.take_commands(link) == []

    hub.register("door", "new-token")  # link reset
    assert hub.authenticate("secret-token") is None and hub.authenticate("new-token") is link
    hub.unregister("door")
    assert hub.authenticate("new-token") is None


def test_phone_source_reports_offline_and_paused():
    import numpy as np
    hub = PhoneHub()
    src = hub.register("door", "t")
    assert src.status()["connected"] is False and "Waiting" in src.status()["error"]
    src.push(np.zeros((48, 64, 3), np.uint8))
    assert src.status()["connected"] is True and src.get_frame()[0] is not None
    src._frame_time = time.time() - 10
    src.touch({"state": "paused"})
    st = src.status()
    assert st["connected"] is False and "paused" in st["error"]
    assert src.get_frame()[0] is None, "stale phone frames are not processed"


def test_certificate_covers_lan_addresses(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_service, "TLS_DIR", tmp_path)
    monkeypatch.setattr(phone_service, "CERT_FILE", tmp_path / "c.crt")
    monkeypatch.setattr(phone_service, "KEY_FILE", tmp_path / "c.key")
    cert_path, _ = ensure_certificate()
    cert = x509.load_pem_x509_certificate(open(cert_path, "rb").read())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    assert "127.0.0.1" in ips and set(lan_addresses()) <= ips
    days = (cert.not_valid_after_utc - cert.not_valid_before_utc).days
    assert days <= 825, "iOS rejects longer-lived TLS certificates"
    mtime = (tmp_path / "c.crt").stat().st_mtime
    ensure_certificate()
    assert (tmp_path / "c.crt").stat().st_mtime == mtime, "reused while still valid"


@pytest.mark.parametrize("path, port, allowed", [
    ("/phone", 8443, True),
    ("/api/phone/frame", 8443, True),
    ("/api/status", 8443, False),
    ("/", 8443, False),
    ("/ws/stream/cam1", 8443, False),
    ("/ws/talk/cam1", 8443, False),
    ("/ws/listen/cam1", 8443, False),
    ("/api/status", 8000, True),
])
def test_phone_port_only_serves_phone_routes(path, port, allowed):
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    guard = PhonePortGuard(inner, 8443)
    kind = "websocket" if path.startswith("/ws") else "http"
    asyncio.run(guard({"type": kind, "path": path, "server": ("0.0.0.0", port), "headers": []}, receive, send))
    assert bool(reached) is allowed
    if not allowed and kind == "http":
        assert sent[0]["status"] == 404
