import time

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api as api_module
from main import app
from services.audio_service import CameraSpeaker
from services.settings_service import SettingsService, settings_service
from services.sound_service import COOLDOWN_SECONDS, CLIP_SECONDS, SoundMonitor, loud_threshold
from services.talk_service import AudioRelay, TalkError, TalkPlayer, audio_hub, find_talk_player

VOICE = b"\x00\x10" * 1600  # 100 ms of 16 kHz PCM


# ---- where the owner's voice plays ---------------------------------------------------
@pytest.mark.parametrize("system, installed, name, mode", [
    ("Linux", {"paplay", "aplay", "ffplay"}, "paplay", "live"),
    ("Linux", {"aplay", "ffplay"}, "aplay", "live"),
    ("Linux", {"ffplay"}, "ffplay", "live"),
    ("Darwin", {"afplay", "ffplay"}, "ffplay", "live"),
    ("Darwin", {"afplay"}, "afplay", "after"),
    ("Windows", set(), "winsound", "after"),
    ("Linux", set(), None, None),
])
def test_talk_player_prefers_live_players(system, installed, name, mode):
    player = find_talk_player(system, lambda exe: f"/usr/bin/{exe}" if exe in installed else None)
    assert (player.name, player.mode) == (name, mode) if name else player is None
    if player and player.mode == "live":
        assert "16000" in " ".join(player.command), "raw 16 kHz PCM on stdin"


class FakeProcess:
    def __init__(self, command, **kwargs):
        self.command = command
        self.written = bytearray()
        self.stdin = self
        self.closed = False

    def write(self, data):
        self.written += data

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return None


def wait_until(check, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


def phone_messages(phone):
    out = []
    while not phone.queue.empty():
        out.append(phone.queue.get_nowait())
    return out


def test_talk_goes_to_the_phone_one_talker_at_a_time():
    relay = AudioRelay("door")
    phone = relay.connect_phone()
    assert phone_messages(phone) == [{"type": "listen", "on": False}]
    talk = relay.start_talk("device")
    assert talk.describe() == {"phone": True, "computer": None}
    with pytest.raises(TalkError) as busy:
        relay.start_talk("device")
    assert busy.value.busy and relay.status()["talking"]
    talk.feed(VOICE)
    talk.feed(b"\x00" * 3)  # not 16-bit samples
    assert phone_messages(phone) == [VOICE]
    for _ in range(30):  # a phone that can't keep up gets a gap rather than a growing delay
        talk.feed(VOICE)
    assert len(phone_messages(phone)) < 30
    assert relay.end_talk(talk) == pytest.approx(3.1)
    assert not relay.talking and relay.talked_recently()
    relay.end_talk(relay.start_talk("device"))  # free again


def test_talk_needs_somewhere_to_play():
    relay = AudioRelay("door")
    with pytest.raises(TalkError, match="phone is not connected"):
        relay.start_talk("device", TalkPlayer("paplay", "live", ("paplay",)))
    with pytest.raises(TalkError, match="no audio player"):
        relay.start_talk("server", None)
    assert not relay.talking
    relay.connect_phone()
    talk = relay.start_talk("both", None)
    assert talk.describe() == {"phone": True, "computer": None}, "the phone alone will do"
    relay.end_talk(talk)


def test_talk_plays_live_on_this_computer():
    relay = AudioRelay("webcam")
    procs = []
    talk = relay.start_talk("server", TalkPlayer("paplay", "live", ("paplay", "--raw")),
                            popen=lambda cmd, **kw: procs.append(FakeProcess(cmd)) or procs[-1])
    assert talk.describe() == {"phone": False, "computer": "live"}
    talk.feed(VOICE)
    talk.feed(VOICE)
    relay.end_talk(talk)
    assert wait_until(lambda: procs[0].closed)
    assert procs[0].command == ["paplay", "--raw"] and bytes(procs[0].written) == VOICE * 2


def test_talk_without_a_live_player_plays_when_released():
    relay = AudioRelay("webcam")
    played = []
    talk = relay.start_talk("both", TalkPlayer("afplay", "after", ("afplay",)),
                            play=lambda player, pcm: played.append((player.name, pcm)))
    assert talk.describe() == {"phone": False, "computer": "after"}
    talk.feed(VOICE)
    assert played == []
    relay.end_talk(talk)
    assert wait_until(lambda: played == [("afplay", VOICE)])


def test_listen_asks_the_phone_while_anyone_listens():
    relay = AudioRelay("door")
    phone = relay.connect_phone()
    phone_messages(phone)
    first, second = relay.add_listener(), relay.add_listener()
    assert phone_messages(phone) == [{"type": "listen", "on": True}], "only the first listener asks"
    relay.phone_audio(VOICE)
    assert first.get_nowait() == VOICE and second.get_nowait() == VOICE
    relay.remove_listener(first)
    assert phone_messages(phone) == []
    for _ in range(50):  # a slow dashboard loses the oldest audio, not the newest
        relay.phone_audio(VOICE)
    assert second.qsize() == second.maxsize
    relay.remove_listener(second)
    assert phone_messages(phone) == [{"type": "listen", "on": False}]

    relay.add_listener()
    again = relay.connect_phone()  # the page reconnects while someone listens
    assert phone_messages(again) == [{"type": "listen", "on": True}]
    assert phone_messages(phone)[-1] is None, "the old connection is told to close"


def test_levels_show_a_live_microphone():
    relay = AudioRelay("door")
    assert relay.status()["mic"] is False
    relay.phone_level(-42.5, now=time.time())
    assert relay.status() == {"link": False, "mic": True, "level_db": -42.5, "talking": False, "listeners": 0}
    assert relay.take_levels()[0][1] == -42.5 and relay.take_levels() == []
    assert relay.status(now=time.time() + 5)["mic"] is False


def test_warnings_wait_while_someone_talks():
    sent = []
    talking = [True]
    speaker = CameraSpeaker("door", lambda: "device", lambda cmd: sent.append(cmd) or True, paused=lambda: talking[0])
    assert speaker.say("Leave now", 165) is False and sent == []
    talking[0] = False
    assert speaker.say("Leave now", 165) is True and speaker.sounding, "its own voice is not a loud sound"


# ---- loud sounds -----------------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.active = False
        self.starts = []
        self.stops = 0

    @property
    def file(self):
        return "clip.mp4" if self.active else None

    def start(self, reason, level=0):
        self.starts.append(reason)
        self.active = True
        return "clip.mp4"

    def stop(self, immediate=False):
        self.stops += 1
        self.active = False


class Notifier:
    def __init__(self, works=True):
        self.works = works
        self.alerts = []

    def send_alert(self, title, message, severity="HIGH", snapshot=None):
        self.alerts.append((title, snapshot))
        return self.works


class Events:
    def __init__(self):
        self.entries = []

    def log(self, event_type, description, severity="INFO", recording=None, camera=None, snapshot=None):
        self.entries.append({"type": event_type, "text": description, "severity": severity, "recording": recording,
                             "camera": camera, "snapshot": snapshot})

    def of(self, event_type):
        return [e for e in self.entries if e["type"] == event_type]


@pytest.fixture
def monitor(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    m = SoundMonitor(lambda: "Porch", Recorder(), settings=settings, notifier=Notifier(), events=Events())
    m.snapshot = lambda: b"picture"
    return m


def quiet_minute(monitor, start=1000.0, db=-50.0, seconds=30):
    t = start
    for _ in range(seconds * 4):
        assert monitor.feed(db, t) is False
        t += 0.25
    return t


def test_threshold_follows_the_usual_level_and_sensitivity():
    assert loud_threshold(-50, 5) == -20, "30 dB above a quiet room"
    assert loud_threshold(-90, 5) == -25, "a silent microphone counts as a quiet room"
    assert loud_threshold(-90, 10) == -40, "never below the floor"
    assert loud_threshold(-30, 1) == -3, "the loudest sounds always count"
    assert loud_threshold(-50, 8) < loud_threshold(-50, 5) < loud_threshold(-50, 2)


def test_baseline_is_the_quiet_end_of_the_last_minute(monitor):
    assert monitor.baseline() is None
    t = quiet_minute(monitor, db=-50)
    for _ in range(10):  # a short noisy moment hardly moves it
        monitor.feed(-30, t)
        t += 0.25
    assert monitor.baseline() == -50
    t = quiet_minute(monitor, start=t + 60, db=-35, seconds=60)
    assert monitor.baseline() == -35, "readings older than a minute are forgotten"


def test_loud_sound_is_logged_with_a_picture_then_cools_down(monitor):
    assert monitor.feed(-5, 1000) is False, "not before it knows what is usual"
    t = quiet_minute(monitor)
    assert monitor.feed(-25, t) is False, "louder, but not loud enough"
    assert monitor.feed(-8, t + 0.25) is True
    [event] = monitor.events.of("SOUND")
    assert event["text"] == "Loud sound (-8 dB, usually -50 dB)" and event["snapshot"] == b"picture"
    assert event["camera"] == "Porch" and event["severity"] == "LOW"
    assert monitor.recorder.starts == [] and monitor.notifier.alerts == [], "log mode only logs"
    assert monitor.feed(-8, t + COOLDOWN_SECONDS - 1) is False
    assert monitor.feed(-8, t + COOLDOWN_SECONDS + 1) is True


def test_own_sounds_are_ignored(monitor):
    t = quiet_minute(monitor)
    monitor.own_sound = lambda: True  # the siren, a warning, or the owner talking through the phone
    assert monitor.feed(-2, t) is False
    monitor.own_sound = lambda: False
    assert monitor.baseline() == -50, "and they don't count towards what is usual"


def test_sound_alerts_off(monitor):
    monitor.settings.update({"detection": {"sound_alerts": "off"}})
    t = quiet_minute(monitor)
    assert monitor.feed(-1, t) is False and monitor.events.entries == []


def test_alert_mode_records_and_alerts_while_armed(monitor):
    monitor.settings.update({"detection": {"sound_alerts": "alert"}})
    t = quiet_minute(monitor)
    assert monitor.feed(-4, t)
    assert monitor.recorder.starts == ["sound"]
    assert [e["type"] for e in monitor.events.entries] == ["SOUND", "RECORDING", "ALERT"]
    assert all(e["recording"] == "clip.mp4" for e in monitor.events.entries)
    assert monitor.notifier.alerts == [("Loud sound at Porch", b"picture")]

    monitor.tick(t + CLIP_SECONDS - 1)
    assert monitor.recorder.stops == 0
    monitor.tick(t + CLIP_SECONDS)
    assert monitor.recorder.stops == 1, "the clip ends by itself"

    assert monitor.feed(-4, t + COOLDOWN_SECONDS + 1)
    assert len(monitor.notifier.alerts) == 1, "alerts are rate limited; the event is still logged"
    assert len(monitor.events.of("SOUND")) == 2

    monitor.incident_active = lambda: True
    monitor.tick(t + COOLDOWN_SECONDS + 1 + CLIP_SECONDS)
    assert monitor.recorder.stops == 1, "an incident that started meanwhile keeps the clip going"


def test_alert_mode_only_logs_while_disarmed(monitor):
    monitor.settings.update({"armed": False, "detection": {"sound_alerts": "alert"}})
    t = quiet_minute(monitor)
    assert monitor.feed(-4, t)
    assert [e["type"] for e in monitor.events.entries] == ["SOUND"]
    assert monitor.recorder.starts == [] and monitor.notifier.alerts == []


# ---- the WebSocket endpoints -------------------------------------------------------------
def receive(ws, seconds=5.0) -> dict:
    """ws.receive() that fails the test instead of hanging when nothing arrives."""
    async def get():
        with anyio.fail_after(seconds):
            return await ws._send_rx.receive()
    message = ws.portal.call(get)
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(message.get("code", 1000))
    return message


def receive_json(ws) -> dict:
    import json
    return json.loads(receive(ws)["text"])


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def phone_camera(client):
    cam = settings_service.add_camera("Porch", "phone")
    try:
        yield cam
    finally:
        settings_service.remove_camera(cam.id)


def camera_status(client, cam_id):
    return next(c for c in client.get("/api/status").json()["cameras"] if c["id"] == cam_id)


def test_phone_audio_link_needs_a_valid_pairing_link(client):
    with client.websocket_connect("/api/phone/audio?k=wrong") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            receive(ws)
    assert closed.value.code == 4401


def test_talk_and_listen_through_a_phone(client, phone_camera):
    cam_id = phone_camera.id
    assert camera_status(client, cam_id)["audio"] == {"output": "device", "link": False, "mic": False,
                                                      "level_db": None, "talking": False, "listeners": 0}
    assert client.get("/api/status").json()["talk"].keys() == {"mode", "player"}
    with client.websocket_connect(f"/api/phone/audio?k={phone_camera.token}") as phone:
        assert receive_json(phone) == {"type": "listen", "on": False}
        phone.send_json({"type": "level", "db": -41.5})
        assert wait_until(lambda: camera_status(client, cam_id)["audio"]["mic"])
        assert camera_status(client, cam_id)["audio"]["level_db"] == -41.5

        with client.websocket_connect(f"/ws/listen/{cam_id}") as listener:
            assert receive_json(listener) == {"type": "ready", "mic": True}
            assert receive_json(phone) == {"type": "listen", "on": True}
            phone.send_bytes(VOICE)
            assert receive(listener)["bytes"] == VOICE
            assert camera_status(client, cam_id)["audio"]["listeners"] == 1
        assert receive_json(phone) == {"type": "listen", "on": False}

        with client.websocket_connect(f"/ws/talk/{cam_id}") as talker:
            assert receive_json(talker) == {"type": "ready", "phone": True, "computer": None}
            with client.websocket_connect(f"/ws/talk/{cam_id}") as other:
                reply = receive_json(other)
                assert reply["type"] == "busy" and "Someone else" in reply["message"]
            r = client.post("/api/speak", json={"text": "Hello", "camera_id": cam_id})
            assert r.status_code == 409, "typed lines would talk over the owner"
            for _ in range(10):
                talker.send_bytes(VOICE)
            for _ in range(10):
                assert receive(phone)["bytes"] == VOICE
            assert camera_status(client, cam_id)["audio"]["talking"] is True
        assert wait_until(lambda: not camera_status(client, cam_id)["audio"]["talking"])
        talks = lambda: client.get("/api/events", params={"type": "TALK", "camera": "Porch"}).json()["items"]  # noqa: E731
        assert wait_until(talks) and talks()[0]["description"] == "Spoke through Porch for 1 s"

        client.post(f"/api/cameras/{cam_id}/reset-link")  # the old link stops working, even for a quiet phone
        with pytest.raises(WebSocketDisconnect) as closed:
            receive(phone)
        assert closed.value.code == 4401
    assert wait_until(lambda: not camera_status(client, cam_id)["audio"]["link"])


def test_talk_needs_a_connected_phone(client, phone_camera):
    with client.websocket_connect(f"/ws/talk/{phone_camera.id}") as talker:
        reply = receive_json(talker)
    assert reply["type"] == "error" and "not connected" in reply["message"]
    assert not audio_hub.relay(phone_camera.id).talking


def test_listen_only_for_phone_cameras(client):
    with client.websocket_connect("/ws/listen/cam1") as ws:
        assert receive_json(ws)["type"] == "error"


def test_talk_and_listen_refuse_other_websites(client, phone_camera, monkeypatch):
    for path in (f"/ws/listen/{phone_camera.id}", f"/ws/talk/{phone_camera.id}"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers={"Origin": "https://evil.example"}) as ws:
                receive(ws)
    with client.websocket_connect(f"/ws/listen/{phone_camera.id}", headers={"Origin": "http://testserver"}) as ws:
        assert receive_json(ws)["type"] == "ready", "the dashboard's own page"
    monkeypatch.setattr(api_module, "PUBLIC_URL", "https://guardian.example.com")
    with client.websocket_connect(f"/ws/listen/{phone_camera.id}",
                                  headers={"Origin": "https://guardian.example.com"}) as ws:
        assert receive_json(ws)["type"] == "ready", "through a reverse proxy or tunnel"
