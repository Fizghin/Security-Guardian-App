import pytest

from models.domain import Detection
from services.brain_service import CameraBrain, voice_cooldown
from services.settings_service import SettingsService


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeAI:
    def __init__(self):
        self.requests = []
        self.pending = []
        self.auto = True
        self.cache: dict[int, str] = {}
        self.last_model = "test-model"

    def take_cached(self, ctx):
        return self.cache.pop(ctx.level, None)

    def request_warning(self, callback, ctx, camera_id=""):
        self.requests.append(ctx)
        result = {"text": f"warning level {ctx.level}", "source": "llm", "model": "test", "latency_ms": 1}
        if self.auto:
            callback(result)
        else:
            self.pending.append((callback, result))
        return True


class FakeRecorder:
    def __init__(self):
        self.active = False
        self.starts = []
        self.stops = 0
        self.on_finished = None

    @property
    def file(self):
        return "clip.mp4" if self.active else None

    def start(self, reason, level=0):
        if not self.active:  # while recording, the clip just continues
            self.starts.append(reason)
        self.active = True
        return "clip.mp4"

    def note_level(self, level):
        pass

    def stop(self, immediate=False):
        self.stops += 1
        self.active = False


class FakeSpeaker:
    def __init__(self):
        self.said = []
        self.siren_active = False
        self.siren_calls = []
        self.talking = False

    def paused(self):
        return self.talking

    def say(self, text, rate=165, interrupt=False):
        self.said.append(text)
        return True

    def siren(self, on, max_seconds=60):
        self.siren_calls.append(on)
        self.siren_active = on
        return True

    def describe(self):
        return "this computer"


class FakeNotifier:
    def __init__(self, works=True):
        self.works = works
        self.alerts = []
        self.clips = []

    def send_alert(self, title, message, severity="HIGH", snapshot=None):
        self.alerts.append(title)
        return self.works

    def send_clip(self, title, message, path):
        self.clips.append(path)
        return True


class FakeEvents:
    def __init__(self):
        self.entries = []
        self.pictures = []
        self.clips = []

    def log(self, event_type, description, severity="INFO", recording=None, camera=None, snapshot=None):
        self.entries.append((event_type, severity, camera))
        self.pictures.append((event_type, snapshot))
        self.clips.append((event_type, recording))

    def types(self):
        return [t for t, _, _ in self.entries]


@pytest.fixture
def brain(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    return CameraBrain("door", lambda: "Front door", FakeSpeaker(), FakeRecorder(), settings=settings, ai=FakeAI(),
                       notifier=FakeNotifier(), events=FakeEvents(), clock=Clock())


def person(status="unknown", name=None, face=False, simulated=False):
    return Detection(class_name="person", confidence=0.9, bbox=[0, 0, 100, 200], status=status,
                     known=status == "known", identity=name, face_visible=face, simulated=simulated)


def advance(brain, seconds, detections):
    brain.clock.t += seconds
    brain.process(detections)


def test_escalates_through_levels_with_default_timings(brain):
    brain.process([person()])
    assert brain.threat_level == 1
    advance(brain, 5, [person()])
    assert brain.threat_level == 2
    assert brain.recorder.starts == ["intruder"]
    advance(brain, 5, [person()])
    assert brain.threat_level == 3
    assert brain.notifier.alerts == ["Intruder at Front door"]
    advance(brain, 5, [person()])
    assert brain.threat_level == 4
    assert brain.speaker.siren_active
    assert brain.events.types().count("ESCALATION") == 3
    assert all(cam == "Front door" for _, _, cam in brain.events.entries)


def test_pending_people_do_not_start_an_incident(brain):
    brain.process([person("pending")])
    advance(brain, 1, [person("pending")])
    assert brain.threat_level == 0
    assert brain.pending == 1
    advance(brain, 1, [person("unknown")])
    assert brain.threat_level == 1


def test_pending_person_keeps_running_incident_alive(brain):
    brain.process([person()])
    for _ in range(12):  # tracker lost and re-found the intruder; still being identified
        advance(brain, 1, [person("pending")])
    assert brain.threat_level >= 1, "incident must not clear while someone is still in view"


def test_clears_after_person_leaves(brain):
    brain.process([person()])
    advance(brain, 6, [person()])
    requests = len(brain.ai.requests)
    advance(brain, 9, [])
    assert brain.threat_level == 2, "level does not climb while nobody is in view"
    assert not brain.speaker.siren_active
    assert len(brain.ai.requests) == requests, "no warnings to an empty scene"
    advance(brain, 1, [])
    assert brain.threat_level == 0
    assert brain.recorder.stops == 1
    assert "CLEARED" in brain.events.types()


def test_insiders_do_not_raise_alarm(brain):
    brain.process([person("known", "Alex", face=True)])
    advance(brain, 20, [person("known", "Alex", face=True)])
    assert brain.threat_level == 0
    assert brain.insiders_in_view == ["Alex"]
    assert brain.events.types().count("INSIDER") == 1, "insider sightings are rate limited"


def test_insider_turning_away_is_still_trusted(brain):
    brain.process([person("known", "Alex", face=True)])
    advance(brain, 3, [person("unknown", face=False)])
    assert brain.threat_level == 0


def test_stranger_with_visible_face_next_to_insider_escalates(brain):
    brain.process([person("known", "Alex", face=True), person("unknown", face=True)])
    assert brain.threat_level == 1


def test_disarmed_ignores_people(brain):
    brain.settings.update({"armed": False})
    brain.process([person()])
    advance(brain, 30, [person()])
    assert brain.threat_level == 0


def test_disarm_ends_incident(brain):
    brain.process([person()])
    brain.disarm()
    assert brain.threat_level == 0
    assert brain.recorder.stops == 1


def test_panic_latches_until_reset(brain):
    brain.enter_panic()
    assert brain.threat_level == 4
    assert brain.speaker.siren_active
    assert brain.recorder.starts == ["panic"]
    assert brain.notifier.alerts == [], "panic alerts are sent once by the camera manager"
    advance(brain, 120, [])
    assert brain.threat_level == 4, "panic does not clear on its own"
    assert brain.reset() is True
    assert brain.threat_level == 0
    assert brain.speaker.siren_calls[-1] is False


def test_first_warning_uses_prepared_line(brain):
    brain.ai.cache[1] = "Hello, can I help you?"
    brain.process([person()])
    assert brain.speaker.said == ["Hello, can I help you?"]
    assert brain.last_message_source == "cached"
    assert brain.ai.requests == [], "no model call needed"


def test_warning_context_reports_real_facts(brain):
    brain.notifier.works = False  # no alert channel configured
    brain.process([person(), person()])
    for _ in range(3):
        advance(brain, 5, [person(), person()])
    ctx = brain.ai.requests[-1]
    assert ctx.level == 4 and ctx.people == 2 and ctx.location == "Front door"
    assert ctx.recording is True
    assert ctx.alerted is False, "an alert that could not be sent must not be claimed"
    assert ctx.siren is True


def test_voice_repeats_according_to_persistence(brain):
    brain.process([person()])
    assert len(brain.ai.requests) == 1
    cooldown = voice_cooldown(brain.settings.get().ai.persistence)
    advance(brain, 2, [person()])
    assert len(brain.ai.requests) == 1
    advance(brain, 3, [person()])  # level 2 reached: speaks immediately
    assert len(brain.ai.requests) == 2
    brain.settings.update({"escalation": {"level3_after": 200, "level4_after": 300}})
    advance(brain, cooldown - 1, [person()])
    assert len(brain.ai.requests) == 2
    advance(brain, 1.5, [person()])
    assert len(brain.ai.requests) == 3


def test_warnings_wait_while_the_owner_talks(brain):
    brain.speaker.talking = True
    brain.process([person()])
    advance(brain, 6, [person()])
    assert brain.threat_level == 2 and brain.recorder.starts == ["intruder"], "everything but the voice carries on"
    assert brain.ai.requests == [] and brain.speaker.said == []
    brain.speaker.talking = False
    advance(brain, 0.5, [person()])
    assert [ctx.level for ctx in brain.ai.requests] == [2], "the current level's warning once they stop"


def test_late_warning_after_person_left_is_dropped(brain):
    brain.ai.auto = False
    brain.process([person()])
    advance(brain, 11, [])  # incident cleared before the model answered
    callback, result = brain.ai.pending[0]
    callback(result)
    assert brain.speaker.said == []
    assert brain.last_message is None


def test_greets_insiders_when_enabled(brain):
    brain.settings.update({"ai": {"greet_insiders": True, "greet_cooldown_minutes": 30}})
    brain.process([person("known", "Alex", face=True)])
    assert len(brain.speaker.said) == 1 and "Alex" in brain.speaker.said[0]
    advance(brain, 60, [person("known", "Alex", face=True)])
    assert len(brain.speaker.said) == 1, "not greeted again within the cooldown"
    advance(brain, 30 * 60, [person("known", "Alex", face=True)])
    assert len(brain.speaker.said) == 2


def test_no_greetings_while_disarmed(brain):
    # Disarming promises that nothing is spoken.
    brain.settings.update({"armed": False, "ai": {"greet_insiders": True}})
    brain.process([person("known", "Alex", face=True)])
    assert brain.speaker.said == []
    assert "GREETING" not in brain.events.types() and "INSIDER" in brain.events.types()
    brain.settings.update({"armed": True})
    advance(brain, 1, [person("known", "Alex", face=True)])
    assert len(brain.speaker.said) == 1


def test_clip_is_sent_for_serious_incidents(brain):
    brain._on_recording_finished({"file": "a.mp4", "path": "/tmp/a.mp4", "duration": 12, "max_level": 3,
                                  "reason": "intruder"})
    brain._on_recording_finished({"file": "b.mp4", "path": "/tmp/b.mp4", "duration": 8, "max_level": 2,
                                  "reason": "intruder"})
    assert brain.notifier.clips == ["/tmp/a.mp4"]
    assert brain.events.types().count("CLIP_SAVED") == 2


def test_voice_cooldown_range():
    assert voice_cooldown(0) == 40
    assert voice_cooldown(100) == pytest.approx(8)


def test_key_events_keep_a_picture(brain):
    brain.snapshot = lambda: b"jpeg"
    brain.process([person()])
    advance(brain, 5, [person()])
    pictures = dict(brain.events.pictures)
    assert pictures["DETECTION"] == b"jpeg" and pictures["ESCALATION"] == b"jpeg"
    assert pictures.get("VOICE") is None and pictures.get("RECORDING") is None


def test_pictures_link_to_the_clip_recording_them(brain):
    brain.snapshot = lambda: b"jpeg"
    brain.process([person()])
    advance(brain, 5, [person()])  # level 2 starts the recording
    advance(brain, 5, [person()])  # level 3 alerts the owner
    brain.process([person("known", "Alex", face=True), person()])
    clips = [(t, c) for t, c in brain.events.clips if t != "VOICE"]
    assert clips[:4] == [("DETECTION", None), ("ESCALATION", "clip.mp4"), ("RECORDING", "clip.mp4"),
                         ("ESCALATION", "clip.mp4")]
    assert ("ALERT", "clip.mp4") in clips and ("INSIDER", "clip.mp4") in clips
