"""Sound recognition and the Guard Bot: speech detection, transcription, replies, recognised sounds, escalation
and the phone's microphone protocol. Models are replaced by fakes, so nothing is downloaded."""
import asyncio
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import services.guard_bot as guard_bot
from main import app
from services.ai_service import AIError, WarningContext
from services.brain_service import SOUND_HOLD_SECONDS, CameraBrain
from services.guard_bot import (MAX_UTTERANCE_SECONDS, REPLY_GAP_SECONDS, GuardBot, ReplyContext, SpeechDetector,
                                SpeechToText, check_answer, write_reply)
from services.settings_service import SettingsService, settings_service
from services.sound_recognition import (COOLDOWN_SECONDS, GROUPS, HOP, WINDOW, SoundModel, SoundRecognizer,
                                        group_scores, sound_model)
from services.talk_service import PCM_RATE, AudioRelay
from test_brain import Clock, FakeAI, FakeEvents, FakeNotifier, FakeRecorder, FakeSpeaker, person

RNG = np.random.default_rng(1)


def noise(seconds: float, db: float = -65.0) -> np.ndarray:
    return (RNG.standard_normal(int(seconds * PCM_RATE)) * 32768 * 10 ** (db / 20)).astype(np.int16)


def voice(seconds: float, db: float = -20.0) -> np.ndarray:
    """Speech-like: a buzzing tone in 300 ms bursts with 100 ms pauses, as between syllables."""
    t = np.arange(int(seconds * PCM_RATE)) / PCM_RATE
    on = (t % 0.4) < 0.3
    tone = np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 540 * t)
    return (tone * on * 32768 * 10 ** (db / 20)).astype(np.int16) + noise(seconds)


def feed_chunks(vad: SpeechDetector, samples: np.ndarray, own: bool = False) -> list[np.ndarray]:
    out = []
    for i in range(0, len(samples), 1600):  # 100 ms chunks, as phones send them
        out += vad.feed(samples[i:i + 1600], own)
    return out


# ---- speech detection ----------------------------------------------------------------------------
def test_vad_finds_one_utterance_in_noise():
    vad = SpeechDetector()
    found = feed_chunks(vad, np.concatenate([noise(2), voice(1.5), noise(1.5)]))
    assert len(found) == 1
    assert 1.4 <= len(found[0]) / PCM_RATE <= 2.2, "the speech, a little before it and a moment after"


def test_vad_ignores_background_noise_and_short_clicks():
    vad = SpeechDetector()
    click = np.concatenate([noise(2), voice(0.1), noise(2)])
    assert feed_chunks(vad, np.concatenate([noise(3, db=-40), click])) == []


def test_vad_cuts_long_speech_at_eight_seconds():
    vad = SpeechDetector()
    found = feed_chunks(vad, np.concatenate([noise(1), voice(11), noise(1.5)]))
    assert len(found) == 2
    assert len(found[0]) / PCM_RATE == pytest.approx(MAX_UTTERANCE_SECONDS, abs=0.05)


def test_vad_skips_guardians_own_voice():
    vad = SpeechDetector()
    feed_chunks(vad, noise(1))
    assert feed_chunks(vad, voice(2), own=True) == []
    assert feed_chunks(vad, noise(1.5)) == []
    # speech cut short by Guardian talking is dropped, not glued to what follows
    feed_chunks(vad, voice(0.6))
    assert feed_chunks(vad, voice(0.5), own=True) == []
    assert feed_chunks(vad, noise(1.5)) == []


# ---- speech to text ------------------------------------------------------------------------------
class FakeWhisper:
    def __init__(self, segments):
        self.segments = segments
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append((audio, options))
        return iter(self.segments), None


def segment(text, no_speech=0.1, logprob=-0.3):
    return SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob)


def test_transcription_loads_the_model_and_drops_what_is_not_speech(tmp_path, monkeypatch):
    fetched = []
    def fetch(url, dest, progress):
        fetched.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
    monkeypatch.setattr(guard_bot, "download", fetch)
    model = FakeWhisper([segment(" I am just looking"), segment(" for the delivery box."),
                         segment(" Thank you.", no_speech=0.9), segment(" mumble", logprob=-1.6)])
    created = []
    stt = SpeechToText(tmp_path, create=lambda path: created.append(path) or model, installed=True)
    assert stt.status("tiny.en")["downloaded"] is False
    assert stt.prepare("tiny.en")
    assert wait_until(lambda: stt.ready_for("tiny.en"))
    assert [u.rsplit("/", 1)[1] for u in fetched] == ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]
    assert "faster-whisper-tiny.en" in fetched[0] and created == [tmp_path / "speech-tiny.en"]
    assert stt.status("tiny.en") == {"installed": True, "state": "ready", "model": "tiny.en", "downloaded": True,
                                     "progress": None, "error": None, "size_mb": 75}

    pcm = (np.ones(PCM_RATE) * 16384).astype(np.int16)
    assert stt.transcribe(pcm) == "I am just looking for the delivery box."
    audio, options = model.calls[0]
    assert audio.dtype == np.float32 and audio.max() == pytest.approx(0.5)
    assert options["language"] == "en" and options["beam_size"] == 1
    assert stt.status("base.en")["state"] == "idle", "another model is not ready just because one is"


def test_speech_to_text_says_what_is_missing(tmp_path):
    stt = SpeechToText(tmp_path, installed=False)
    assert stt.prepare("tiny.en") is False
    status = stt.status("tiny.en")
    assert status["state"] == "missing" and "pip install faster-whisper" in status["error"]


def test_failed_download_is_reported_and_retried_only_when_asked(tmp_path, monkeypatch):
    def fail(url, dest, progress):
        raise OSError("network unreachable")
    monkeypatch.setattr(guard_bot, "download", fail)
    stt = SpeechToText(tmp_path, create=lambda path: None, installed=True)
    stt.prepare("tiny.en")
    assert wait_until(lambda: stt.state == "error")
    assert "network unreachable" in stt.status("tiny.en")["error"]
    assert stt.prepare("tiny.en") is False and stt.state == "error", "no retry loop"
    assert stt.prepare("tiny.en", retry=True) and wait_until(lambda: stt.state == "error")


# ---- replies -------------------------------------------------------------------------------------
def reply_ctx(alerted=False, recording=True, said=(), instructions="Deliveries go to the side door"):
    w = WarningContext(level=2, location="Porch", recording=recording, alerted=alerted, said=list(said))
    return ReplyContext(w, "02:15", [("guardian", "Hello, can I help you?"),
                                     ("person", "I am just looking for the delivery box")], instructions)


@pytest.mark.parametrize("text, problem", [
    ("Leave now, the police are on the way.", "police"),
    ("Officers have been dispatched to this address.", "Officers"),
    ("The owner has been alerted, so leave the porch now.", "owner was alerted"),
    ("There are no deliveries scheduled for tonight, so please leave.", "schedule"),
    ("Nobody is home right now, so please leave.", "who is home"),
    ("Come in and leave the parcel by the door.", "invites them in"),
    ("I am Sarah and you need to go now.", "name"),
])
def test_replies_that_claim_untrue_things_are_rejected(text, problem):
    assert problem in check_answer(text, reply_ctx())


def test_true_replies_pass():
    assert check_answer("Deliveries go to the side door, so please leave this porch now.", reply_ctx()) is None
    assert check_answer("The owner has been alerted, so please leave the porch now.", reply_ctx(alerted=True)) is None
    assert "repeats" in check_answer("Hello, can I help you today?", reply_ctx(said=["Hello, can I help you?"]))


class ReplyAI:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked = []
        self.rejected = 0

    def ask(self, messages, temperature=0.7):
        self.asked.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, "test-model"

    def submit(self, job):
        job()


def test_reply_prompt_has_the_facts_and_only_one_sentence_is_kept(tmp_path):
    ai = ReplyAI("Deliveries go to the side door, so please leave this porch. Thank you.")
    result = write_reply(reply_ctx(), ai, SettingsService(tmp_path / "s.json"))
    assert result["text"] == "Deliveries go to the side door, so please leave this porch."
    assert result["source"] == "llm" and result["model"] == "test-model"
    prompt = ai.asked[0][1]["content"]
    for fact in ("Camera location: Porch", "Local time: 02:15", "Threat level: 2 of 4",
                 "Video is being recorded: yes", "The owner has been alerted: no",
                 "The owner's instructions: Deliveries go to the side door",
                 "Person: I am just looking for the delivery box"):
        assert fact in prompt


def test_rejected_reply_is_retried_then_falls_back(tmp_path):
    settings = SettingsService(tmp_path / "s.json")
    ai = ReplyAI("Police are coming for you.", "Please take deliveries to the side door and leave this porch.")
    assert write_reply(reply_ctx(), ai, settings)["source"] == "llm" and ai.rejected == 1
    assert "not acceptable" in ai.asked[1][-1]["content"]

    ai = ReplyAI("Police are coming for you.", "The owner has been alerted.")
    result = write_reply(reply_ctx(), ai, settings)
    assert result["source"] == "fallback" and ai.rejected == 2 and "rejected" in result["error"]
    assert check_reply_is_true(result["text"])

    result = write_reply(reply_ctx(), ReplyAI(AIError("Cannot reach ollama")), settings)
    assert result["source"] == "fallback" and result["error"] == "Cannot reach ollama"


def check_reply_is_true(text):
    from services.ai_service import check_reply
    return check_reply(text, reply_ctx().warning) is None


# ---- the Guard Bot on a camera -------------------------------------------------------------------
class FakeSTT:
    installed = True

    def __init__(self):
        self.prepared = []
        self.ready = True

    def ready_for(self, name):
        return self.ready

    def prepare(self, name, retry=False):
        self.prepared.append(name)
        return True

    def status(self, name):
        return {"state": "ready" if self.ready else "loading", "progress": None, "error": None}

    def submit(self, bot, chunk, own):
        bot.process(chunk, own)

    def transcribe(self, samples):
        return "I am just looking for the delivery box"


class Relay:
    def __init__(self):
        self.wanted = {}
        self.talking = False

    def want(self, purpose, on):
        self.wanted[purpose] = on

    def mic_live(self, now=None):
        return True

    def talked_recently(self, now=None):
        return self.talking

    def streaming(self, now=None):
        return True


REPLIES = ["Please step away from the front door now.", "Deliveries go to the side door, not this porch.",
           "This is private property, so please move along.", "You need to leave the porch right away.",
           "Take your business elsewhere and leave this porch."]


@pytest.fixture
def scene(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    clock = Clock()
    events = FakeEvents()
    brain = CameraBrain("porch", lambda: "Porch", FakeSpeaker(), FakeRecorder(), settings=settings, ai=FakeAI(),
                        notifier=FakeNotifier(), events=events, clock=clock)
    relay = Relay()
    ai = ReplyAI(*REPLIES)
    bot = GuardBot(lambda: "Porch", brain, relay, settings=settings, events=events, ai=ai, stt=FakeSTT(), clock=clock)
    return SimpleNamespace(settings=settings, clock=clock, events=events, brain=brain, relay=relay, ai=ai, bot=bot,
                           speaker=brain.speaker)


def intrusion(scene):
    scene.brain.process([person()])
    scene.bot.tick()


def test_guard_bot_listens_only_during_an_intrusion(scene):
    scene.bot.tick()
    assert scene.relay.wanted["speech"] is False and scene.bot.status()["active"] is False
    intrusion(scene)
    assert scene.relay.wanted["speech"] is True and scene.bot.listening
    scene.settings.update({"ai": {"guard_bot": False}})
    scene.bot.tick()
    assert scene.relay.wanted["speech"] is False
    assert "off" in scene.bot.status()["note"]


def test_guard_bot_hears_and_answers_once_per_six_seconds(scene):
    intrusion(scene)
    audio = np.concatenate([noise(1), voice(1.5), noise(1.5)])
    for i in range(0, len(audio), 1600):
        scene.bot.feed(audio[i:i + 1600].tobytes())
    assert ("VOICE", 'Person said: "I am just looking for the delivery box"') in scene.events.descriptions
    scene.bot.tick()
    assert scene.speaker.said[-1] == REPLIES[0]
    assert any(d.startswith(f"Replied: {REPLIES[0]}") for t, d in scene.events.descriptions if t == "VOICE")
    entries = [(e["who"], e["text"]) for e in scene.bot.status()["entries"]]
    assert entries[-2:] == [("person", "I am just looking for the delivery box"), ("guardian", REPLIES[0])]

    scene.bot.heard("Where is the box then?", scene.brain.incident_id)
    scene.clock.t += REPLY_GAP_SECONDS - 1
    scene.brain.process([person()])
    scene.bot.tick()
    assert len(scene.ai.asked) == 1, "at most one reply every six seconds"
    scene.clock.t += 1
    scene.brain.process([person()])
    scene.bot.tick()
    assert len(scene.ai.asked) == 2 and scene.speaker.said[-1] == REPLIES[1]


def test_guard_bot_stays_silent_while_the_owner_talks(scene):
    intrusion(scene)
    scene.relay.talking = True
    scene.bot.heard("Hello?", scene.brain.incident_id)
    scene.bot.tick()
    assert scene.ai.asked == []
    scene.relay.talking = False
    scene.brain.speak("Please wait at the gate.")  # the owner's "Say instead…"
    scene.bot.tick()
    assert scene.ai.asked == [], "the owner answered instead"
    assert scene.bot.status()["entries"][-1]["who"] == "owner"


def test_guard_bot_stops_when_the_incident_ends(scene):
    intrusion(scene)
    scene.bot.heard("Hello?", scene.brain.incident_id)
    answer = []
    scene.ai.submit = answer.append  # the model is still writing when the person leaves
    scene.bot.tick()
    scene.clock.t += 30
    scene.brain.tick()
    scene.bot.tick()
    said = list(scene.speaker.said)
    answer[0]()
    assert scene.speaker.said == said, "a late reply is not spoken"
    assert scene.relay.wanted["speech"] is False and scene.bot.status() == {
        "active": False, "listening": False, "thinking": False, "note": None, "entries": []}


# ---- sound recognition ---------------------------------------------------------------------------
class ScriptedModel:
    """Stands in for the classifier: each window gets the next scores from a script."""
    ready = True

    def __init__(self, *windows):
        self.windows = list(windows)
        self.seen = []

    def classify(self, samples):
        self.seen.append(len(samples))
        return self.windows.pop(0) if self.windows else {}

    def submit(self, recognizer, chunk, own):
        recognizer.process(chunk, own)

    def prepare(self, retry=False):
        return True


@pytest.fixture
def hearing(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    clock = Clock()
    events, notifier = FakeEvents(), FakeNotifier()
    brain = CameraBrain("porch", lambda: "Porch", FakeSpeaker(), FakeRecorder(), settings=settings, ai=FakeAI(),
                        notifier=notifier, events=events, clock=clock)
    rec = SoundRecognizer(lambda: "Porch", Relay(), brain, brain.recorder, settings=settings, notifier=notifier,
                          events=events, model=ScriptedModel(), clock=clock)
    rec.snapshot = lambda: b"picture"
    return SimpleNamespace(settings=settings, clock=clock, events=events, notifier=notifier, brain=brain, rec=rec)


def test_classes_map_to_plain_labels():
    scores = group_scores({"Shatter": 0.4, "Glass": 0.7, "Bark": 0.2, "Smoke detector, smoke alarm": 0.5,
                           "Speech": 0.9})
    assert scores["glass"] == 0.7 and scores["dog"] == 0.2 and scores["alarm"] == 0.5 and scores["siren"] == 0.0
    assert {g.label for g in GROUPS.values()} == {
        "Glass breaking", "Smoke or fire alarm", "Scream or shout", "Gunshot or explosion", "Banging or knocking",
        "Dog barking", "Baby crying", "Siren"}
    assert {k for k, g in GROUPS.items() if g.severity == "HIGH"} == {"glass", "alarm", "scream", "gunshot"}


def test_windows_are_975_ms_every_half_second(hearing):
    model = hearing.rec.model
    for _ in range(25):  # 2.5 s in 100 ms chunks
        hearing.rec.process(np.zeros(1600, np.int16).tobytes())
    assert model.seen == [WINDOW] * (1 + (25 * 1600 - WINDOW) // HOP)


def test_a_sound_counts_in_two_of_three_windows_then_cools_down(hearing):
    rec, dog = hearing.rec, {"dog": 0.8}
    assert rec.judge(dog) == [] and rec.judge({}) == [], "one window is not enough"
    assert rec.judge(dog) == ["dog"], "two of the last three"
    sound = hearing.events.descriptions[-1]
    assert sound == ("SOUND", "Dog barking heard on Porch (score 0.80)")
    assert hearing.events.pictures[-1] == ("SOUND", b"picture")
    assert rec.judge(dog) == [] and rec.judge(dog) == [], "cooldown"
    hearing.clock.t += COOLDOWN_SECONDS
    assert rec.judge(dog) == ["dog"], "still barking after the cooldown"
    assert rec.judge({"dog": 0.45}) == [] and rec.judge({"dog": 0.45}) == [], "below the threshold"


def test_own_sounds_and_ignored_kinds_do_not_count(hearing):
    rec = hearing.rec
    assert rec.judge({"siren": 0.9}, own=True) == [] and rec.judge({"siren": 0.9}, own=True) == []
    assert rec.judge({"siren": 0.9}) == [], "Guardian's own siren never counts towards it"
    hearing.settings.update({"detection": {"sound_actions": {"banging": "off"}}})
    assert rec.judge({"banging": 0.9}) == [] and rec.judge({"banging": 0.9}) == []


def test_own_sound_marks_the_windows_it_overlaps(hearing):
    rec = hearing.rec
    rec.model = ScriptedModel(*[{"Glass": 0.9}] * 6)
    loud = np.zeros(HOP, np.int16).tobytes()
    rec.process(np.zeros(WINDOW - HOP, np.int16).tobytes())
    assert rec.process(loud, own=True) == [] and rec.process(loud) == [], "both windows overlap the siren"
    assert rec.process(loud) == [] and rec.process(loud) == ["glass"]


def test_high_severity_sound_raises_level_three_and_records_while_armed(hearing):
    hearing.settings.update({"armed": True})
    rec, brain = hearing.rec, hearing.brain
    rec.judge({"glass": 0.6})
    assert rec.judge({"glass": 0.3}) == ["glass"]
    assert brain.threat_level == 3 and brain.recorder.active and brain.incident_active
    assert ("ESCALATION", "Threat level 3 (Intruder): glass breaking heard") in hearing.events.descriptions
    assert ("SOUND", "Glass breaking heard on Porch (score 0.60)") in hearing.events.descriptions
    assert hearing.notifier.alerts == ["Glass breaking at Porch"]
    assert hearing.notifier.messages[0].startswith("Glass breaking heard at Porch. Nobody seen on camera yet.")
    assert brain.speaker.said == [], "nothing is said to a sound"
    assert brain.status()["heard"] == "Glass breaking"

    hearing.clock.t += SOUND_HOLD_SECONDS - 1
    brain.tick()
    assert brain.incident_active
    hearing.clock.t += 1
    brain.tick()
    assert not brain.incident_active and not brain.recorder.active
    assert hearing.events.descriptions[-1][1].startswith("Nobody was seen after the sound")


def test_person_after_a_sound_gets_the_usual_warnings(hearing):
    hearing.settings.update({"armed": True})
    hearing.brain.sound_alarm("Scream or shout")
    hearing.clock.t += 20
    hearing.brain.process([person()])
    assert hearing.brain.threat_level == 3, "time before they were seen does not count"
    assert hearing.brain.speaker.said, "now there is someone to speak to"
    assert ("DETECTION", "Unrecognised person detected") in hearing.events.descriptions


def test_other_alert_sounds_record_a_clip_and_alert_while_armed(hearing):
    hearing.settings.update({"armed": True, "detection": {"sound_actions": {"banging": "alert"}}})
    hearing.rec.judge({"banging": 0.9})
    hearing.rec.judge({"banging": 0.9})
    assert hearing.brain.threat_level == 0 and hearing.brain.recorder.starts == ["sound"]
    assert hearing.notifier.alerts == ["Banging or knocking at Porch"]


def test_safety_sounds_alert_even_while_disarmed(hearing):
    hearing.settings.update({"armed": False})
    rec = hearing.rec
    for kind in ("alarm", "baby", "glass"):
        rec.judge({kind: 0.9})
        rec.judge({kind: 0.9})
    assert hearing.notifier.alerts == ["Smoke or fire alarm at Porch", "Baby crying at Porch"]
    assert "disarmed" in hearing.notifier.messages[0]
    assert hearing.brain.threat_level == 0 and hearing.brain.recorder.starts == []
    assert [t for t in hearing.events.types() if t == "SOUND"] == ["SOUND"] * 3, "glass is still logged"


def test_sound_model_reports_what_is_missing(tmp_path):
    model = SoundModel(tmp_path, installed=False)
    assert model.prepare() is False
    assert model.status()["state"] == "missing" and "pip install mediapipe" in model.status()["error"]


def test_sound_model_downloads_on_first_use(tmp_path, monkeypatch):
    import services.sound_recognition as sr
    monkeypatch.setattr(sr, "download", lambda url, dest, progress: progress(0.5) or dest.write_bytes(b"model"))
    model = SoundModel(tmp_path, create=lambda path: (lambda samples: {"Glass": 0.9}), installed=True)
    assert model.prepare()
    assert wait_until(lambda: model.ready)
    assert model.status()["state"] == "ready" and (tmp_path / "yamnet.tflite").exists()


# ---- the phone's microphone protocol --------------------------------------------------------------
def messages(phone):
    out = []
    while not phone.queue.empty():
        out.append(phone.queue.get_nowait())
    return out


def test_server_asks_the_phone_for_its_microphone():
    relay = AudioRelay("porch")
    got = []
    relay.analyse = got.append
    phone = relay.connect_phone()
    messages(phone)
    relay.phone_audio(b"\x00\x01" * 1600)
    assert got == [], "nothing is analysed unless asked for"
    relay.want("sounds", True)
    relay.want("sounds", True)
    relay.want("speech", True)
    assert messages(phone) == [{"type": "analyse", "sounds": True, "speech": False},
                               {"type": "analyse", "sounds": True, "speech": True}]
    relay.phone_audio(b"\x00\x01" * 1600)
    assert len(got) == 1 and relay.streaming()
    again = relay.connect_phone()  # the page reconnects
    assert messages(again) == [{"type": "listen", "on": False}, {"type": "analyse", "sounds": True, "speech": True}]
    relay.want("sounds", False)
    relay.want("speech", False)
    assert messages(again)[-1] == {"type": "analyse", "sounds": False, "speech": False}


def test_requests_from_camera_threads_reach_the_phone_on_its_loop():
    relay = AudioRelay("porch")

    async def run():
        phone = relay.connect_phone()
        thread = threading.Thread(target=relay.want, args=("speech", True))
        thread.start()
        thread.join()
        return await asyncio.wait_for(phone.queue.get(), 1), await asyncio.wait_for(phone.queue.get(), 1)

    assert asyncio.run(run()) == ({"type": "listen", "on": False}, {"type": "analyse", "sounds": False, "speech": True})


def wait_until(check, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_phone_camera_streams_for_recognition_through_the_api(monkeypatch):
    from test_audio import receive_json
    monkeypatch.setattr(sound_model, "_classify", lambda samples: {})
    with TestClient(app) as client:
        cam = settings_service.add_camera("Porch", "phone")
        try:
            settings_service.update({"detection": {"sound_recognition": True}})
            status = lambda: next(c for c in client.get("/api/status").json()["cameras"] if c["id"] == cam.id)  # noqa: E731
            with client.websocket_connect(f"/api/phone/audio?k={cam.token}") as phone:
                assert receive_json(phone) == {"type": "listen", "on": False}
                phone.send_json({"type": "level", "db": -50})
                assert receive_json(phone) == {"type": "analyse", "sounds": True, "speech": False}
                phone.send_bytes(b"\x00\x01" * 1600)
                assert wait_until(lambda: status()["sound_recognition"])
                assert status()["guard_bot"]["active"] is False
                settings_service.update({"detection": {"sound_recognition": False}})
                assert receive_json(phone) == {"type": "analyse", "sounds": False, "speech": False}
            models = client.get("/api/audio-models").json()
            assert set(models) == {"speech", "sounds"} and models["speech"]["model"] == "tiny.en"
            assert client.post("/api/audio-models/other/download").status_code == 404
        finally:
            settings_service.update({"detection": {"sound_recognition": False}})
            settings_service.remove_camera(cam.id)
