import os
import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api as api_module
from services import camera_access, sources


@pytest.fixture
def run_server():
    cwd = os.getcwd()
    import run_server as module  # it changes into backend/ when imported
    os.chdir(cwd)
    return module


def api_client() -> TestClient:
    app = FastAPI()
    app.include_router(api_module.router)
    return TestClient(app)


@pytest.fixture
def busy_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        yield s.getsockname()[1]


def test_free_port_is_kept(run_server):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert run_server.choose_port("127.0.0.1", port) == (port, False)


def test_port_used_by_another_program_moves_to_the_next_free_one(run_server, busy_port, monkeypatch):
    monkeypatch.setattr(run_server, "guardian_running", lambda host, port: False)
    chosen, running = run_server.choose_port("127.0.0.1", busy_port)
    assert not running and chosen > busy_port and not run_server.port_in_use("127.0.0.1", chosen)


def test_guardian_already_running_is_reused(run_server, busy_port, monkeypatch):
    monkeypatch.setattr(run_server, "guardian_running", lambda host, port: True)
    assert run_server.choose_port("0.0.0.0", busy_port) == (busy_port, True)


def test_second_start_finds_guardian_on_the_fallback_port(run_server, monkeypatch):
    # Another program has 8000, Guardian moved to 8001 earlier: starting again must not open 8002.
    monkeypatch.setattr(run_server, "port_in_use", lambda host, port: port in (8000, 8001))
    monkeypatch.setattr(run_server, "guardian_running", lambda host, port: port == 8001)
    assert run_server.choose_port("127.0.0.1", 8000) == (8001, True)


def test_health_check_identifies_guardian():
    with api_client() as client:
        assert client.get("/api/health").json()["app"] == "guardian"


def test_camera_permission_is_only_checked_on_macos():
    if camera_access.platform.system() != "Darwin":
        assert camera_access.camera_permission() is None
        assert camera_access.local_camera_blocked() is None


@pytest.mark.parametrize("status, blocked", [
    (None, False), (camera_access.AUTHORIZED, False),
    (camera_access.DENIED, True), (camera_access.RESTRICTED, True), (camera_access.NOT_DETERMINED, True),
])
def test_blocked_message_follows_the_permission(monkeypatch, status, blocked):
    monkeypatch.setattr(camera_access, "camera_permission", lambda: status)
    message = camera_access.local_camera_blocked()
    assert bool(message) is blocked
    if status == camera_access.DENIED:
        assert "Privacy & Security" in message


def test_blocked_camera_is_not_opened(monkeypatch):
    # Without permission, OpenCV would fail and print the same warnings on every retry.
    monkeypatch.setattr(sources, "local_camera_blocked", lambda: "macOS is blocking the camera.")
    monkeypatch.setattr(sources.cv2, "VideoCapture", lambda *a, **k: pytest.fail("camera was opened"))
    assert sources.open_local_camera(0) is None
    with pytest.raises(RuntimeError, match="blocking"):
        sources.CaptureSource("auto")._open()
    assert sources.grab_test_frame("0") == (None, "macOS is blocking the camera.")
    # Files and streams don't need the permission
    assert sources.grab_test_frame("/no/such/file.mp4")[1].startswith("File not found")


def test_scan_explains_a_blocked_camera(monkeypatch):
    monkeypatch.setattr(api_module, "local_camera_blocked", lambda: "macOS is blocking the camera.")
    with api_client() as client:
        r = client.get("/api/cameras/scan")
    assert r.status_code == 409 and "blocking" in r.json()["detail"]


def test_guardian_on_a_fallback_port_is_found_after_the_original_frees_up(run_server, monkeypatch):
    # Guardian moved to 8001 while 8000 was busy; 8000 is free again now. A second start must not use it.
    monkeypatch.setattr(run_server, "port_in_use", lambda host, port: port == 8001)
    monkeypatch.setattr(run_server, "guardian_running", lambda host, port: port == 8001)
    assert run_server.choose_port("127.0.0.1", 8000) == (8001, True)


def test_port_held_on_another_address_counts_as_busy(run_server):
    # Nothing answers on 127.0.0.1, but binding 0.0.0.0 would still fail.
    with socket.socket() as s:
        s.bind(("127.0.0.2", 0))
        s.listen()
        port = s.getsockname()[1]
        assert run_server.port_in_use("0.0.0.0", port)


def test_ipv6_host_does_not_crash(run_server):
    assert isinstance(run_server.port_in_use("::1", 1), bool)
    assert run_server._local("::") == "[::1]" and run_server._local("0.0.0.0") == "127.0.0.1"


def test_older_guardian_is_recognised_by_its_api_title(run_server, monkeypatch):
    class Reply:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    replies = {"/api/health": {"ok": True, "time": 1.0}, "/openapi.json": {"info": {"title": "Guardian"}}}
    monkeypatch.setattr(run_server.httpx, "get", lambda url, **kw: Reply(replies[url[url.index("/", 8):]]))
    assert run_server.guardian_running("127.0.0.1", 8000)
    replies["/openapi.json"] = {"info": {"title": "CityEye"}}
    assert not run_server.guardian_running("127.0.0.1", 8000)
