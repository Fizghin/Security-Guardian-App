import base64

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main import DashboardPassword
from services import phone_service


def basic(password: str, user: str = "guardian") -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def client():
    inner = FastAPI()

    @inner.get("/api/status")
    def status():
        return {"ok": True}

    @inner.get("/api/health")
    def health():
        return {"ok": True}

    @inner.get("/phone")
    def phone():
        return {"page": True}

    @inner.websocket("/ws/stream/cam1")
    async def stream(ws: WebSocket):
        await ws.accept()
        await ws.send_text("frame")
        await ws.close()

    inner.add_middleware(DashboardPassword, password="s3cret")
    return TestClient(inner)


def test_dashboard_asks_for_the_password(client):
    r = client.get("/api/status")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/api/status", headers=basic("wrong")).status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Basic !!notbase64"}).status_code == 401


def test_password_sets_a_session_cookie_for_the_video_socket(client):
    r = client.get("/api/status", headers=basic("s3cret"))
    assert r.status_code == 200 and "HttpOnly" in r.headers["set-cookie"]
    # The cookie alone now works, including for the WebSocket the browser can't add a password to.
    assert client.get("/api/status").status_code == 200
    with client.websocket_connect("/ws/stream/cam1") as ws:
        assert ws.receive_text() == "frame"


def test_websocket_without_login_is_refused(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/stream/cam1") as ws:
            ws.receive_text()
    client.cookies.set(DashboardPassword.COOKIE, "forged")
    assert client.get("/api/status").status_code == 401


def test_phones_and_health_check_need_no_password(client):
    assert client.get("/phone").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_no_password_means_open(client):
    inner = FastAPI()
    inner.get("/api/status")(lambda: {"ok": True})
    inner.add_middleware(DashboardPassword, password="")
    assert TestClient(inner).get("/api/status").status_code == 200


def test_public_url_comes_first_in_pairing_links(monkeypatch):
    monkeypatch.setattr(phone_service, "PUBLIC_URL", "https://guardian.example.com")
    monkeypatch.setattr(phone_service, "PHONE_PORT", 8443)
    monkeypatch.setattr(phone_service, "lan_addresses", lambda: ["192.168.1.20"])
    assert phone_service.pairing_urls("tok") == ["https://guardian.example.com/phone?k=tok",
                                                 "https://192.168.1.20:8443/phone?k=tok"]
