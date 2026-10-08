import pytest

from models.domain import Detection
from services.brain_service import BrainService, voice_cooldown
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

    def request_warning(self, callback, **kwargs):
        self.requests.append(kwargs)
        result = {"text": f"warning level {kwargs['level']}", "source": "llm", "model": "test", "latency_ms": 1}
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

    def start(self, reason, level=0):
        self.starts.append(reason)
        self.active = True
        return "clip.mp4"

    def note_level(self, level):
        pass

    def stop(self, immediate=False):
        self.stops += 1
        self.active = False

    def prune(self, days):
        return 0


class FakeSiren:
    def __init__(self):
        self.active = False
        self.starts = 0

    def start(self, max_seconds=60):
        self.active = True
        self.starts += 1
        return True

    def stop(self):
        self.active = False


class FakeTTS:
    def __init__(self):
        self.said = []

    def say(self, text, rate=165, interrupt=False):
        self.said.append(text)
        return True


class FakeNotifier:
    def __init__(self):
        self.alerts = []
        self.clips = []

    def send_alert(self, title, message, severity="HIGH", snapshot=None):
        self.alerts.append(title)
        return True

    def send_clip(self, title, message, path):
        self.clips.append(path)
        return True


class FakeEvents:
    def __init__(self):
        self.log_entries = []

    def log(self, event_type, description, severity="INFO", recording=None):
        self.log_entries.append((event_type, severity))

    def types(self):
        return [t for t, _ in self.log_entries]


@pytest.fixture
def brain(tmp_path):
    settings = SettingsService(tmp_path / "settings.json")
    b = BrainService(settings=settings, ai=FakeAI(), tts=FakeTTS(), siren=FakeSiren(), recorder=FakeRecorder(),
                     notifier=FakeNotifier(), events=FakeEvents(), clock=Clock())
    return b


def person(known=False, name=None, face=False, simulated=False):
    return Detection(class_name="person", confidence=0.9, bbox=[0, 0, 100, 200], known=known, identity=name,
                     face_visible=face, simulated=simulated)


def advance(brain, seconds, detections):
    brain.clock.t += seconds
    brain.process(detections)


def test_escalates_through_levels_with_default_timings(brain):
    brain.process([person()])
    assert brain.threat_level == 1
    advance(brain, 5, [person()])
    assert brain.threat_level == 2
    assert brain.recorder.starts == ["intruder"]  # records from level 2 by default
    advance(brain, 5, [person()])
    assert brain.threat_level == 3
    assert brain.notifier.alerts, "owner is alerted at level 3"
    advance(brain, 5, [person()])
    assert brain.threat_level == 4
    assert brain.siren.active
    assert brain.events.types().count("ESCALATION") == 3


def test_clears_after_person_leaves(brain):
    brain.process([person()])
    advance(brain, 6, [person()])
    requests = len(brain.ai.requests)
    advance(brain, 9, [])
    assert brain.threat_level == 2, "level does not climb while nobody is in view"
    assert not brain.siren.active
    assert len(brain.ai.requests) == requests, "no warnings to an empty scene"
    advance(brain, 1, [])
    assert brain.threat_level == 0
    assert brain.recorder.stops == 1
    assert "CLEARED" in brain.events.types()


def test_custom_escalation_settings(brain):
    brain.settings.update({"escalation": {"level2_after": 2, "level3_after": 4, "level4_after": 30,
                                          "record_at_level": 3, "siren_enabled": False}})
    brain.process([person()])
    advance(brain, 2, [person()])
    assert brain.threat_level == 2
    assert brain.recorder.starts == []
    advance(brain, 2, [person()])
    assert brain.threat_level == 3
    assert brain.recorder.starts == ["intruder"]
    advance(brain, 26, [person()])
    assert brain.threat_level == 4
    assert brain.siren.starts == 0


def test_insiders_do_not_raise_alarm(brain):
    brain.process([person(known=True, name="Alex", face=True)])
    advance(brain, 20, [person(known=True, name="Alex", face=True)])
    assert brain.threat_level == 0
    assert brain.insiders_in_view == ["Alex"]
    assert brain.events.types().count("INSIDER") == 1, "insider sightings are rate limited"


def test_insider_turning_away_is_still_trusted(brain):
    brain.process([person(known=True, name="Alex", face=True)])
    advance(brain, 3, [person(face=False)])  # same person, face not visible
    assert brain.threat_level == 0


def test_stranger_with_visible_face_next_to_insider_escalates(brain):
    brain.process([person(known=True, name="Alex", face=True), person(face=True)])
    assert brain.threat_level == 1


def test_disarmed_ignores_people(brain):
    brain.set_armed(False)
    brain.process([person()])
    advance(brain, 30, [person()])
    assert brain.threat_level == 0
    assert "DISARMED" in brain.events.types()


def test_disarming_ends_incident(brain):
    brain.process([person()])
    brain.set_armed(False)
    assert brain.threat_level == 0
    assert brain.recorder.stops == 1


def test_panic_latches_until_reset(brain):
    brain.trigger_panic()
    assert brain.threat_level == 4
    assert brain.siren.active
    assert brain.recorder.starts == ["panic"]
    advance(brain, 120, [])
    assert brain.threat_level == 4, "panic does not clear on its own"
    assert brain.reset_alarm() is True
    assert brain.threat_level == 0
    assert not brain.siren.active


def test_voice_repeats_according_to_persistence(brain):
    brain.process([person()])
    assert len(brain.ai.requests) == 1
    assert brain.tts.said == ["warning level 1"]
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


def test_late_warning_after_person_left_is_dropped(brain):
    brain.ai.auto = False
    brain.process([person()])
    advance(brain, 11, [])  # incident cleared before the model answered
    callback, result = brain.ai.pending[0]
    callback(result)
    assert brain.tts.said == []
    assert brain.last_message is None


def test_voice_disabled_still_records_message(brain):
    brain.settings.update({"ai": {"voice_enabled": False}})
    brain.process([person()])
    assert brain.tts.said == []
    assert brain.last_message == "warning level 1"


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
