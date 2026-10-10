from datetime import datetime, timedelta

from services.digest_service import DigestScheduler, build
from services.settings_service import DigestSettings


class FakeVault:
    def __init__(self, ok=True):
        self.ok = ok

    def verify_chain(self):
        return {"ok": self.ok}

    def sealed_files(self):
        return {"a.mp4", "b.mp4"}


class FakeLibrary:
    def __init__(self, clips):
        self.clips = clips

    def list(self):
        return self.clips


def fake_insights(incidents=0, alerts=0, level="calm", score=0):
    def make(days, now=None):
        return {"last_24h": {"incidents": incidents, "alerts": alerts, "sirens": 0, "sounds": 0, "unattended": 0,
                             "night_incidents": 1 if incidents else 0},
                "threat": {"score": score, "level": level, "reasons": ["2 owner alerts"] if alerts else []},
                "findings": [{"kind": "alert", "text": "Last night was unusually busy"}] if incidents else []}
    return make


def test_busy_day_digest():
    now = datetime.now().astimezone()
    clip = {"started": (now - timedelta(hours=2)).isoformat(), "max_level": 3}
    old = {"started": (now - timedelta(days=3)).isoformat(), "max_level": 4}
    d = build(now, FakeVault(), FakeLibrary([clip, old]), fake_insights(4, 2, "elevated", 50))
    assert not d["quiet"] and d["severity"] == "MEDIUM"
    assert "Incidents: 4 (1 at night)" in d["message"] and "Clips recorded: 1, peak level 3" in d["message"]
    assert "• 2 owner alerts" in d["message"] and "! Last night was unusually busy" in d["message"]
    assert "intact, 2 clips sealed" in d["message"]


def test_quiet_day_and_broken_chain():
    d = build(None, FakeVault(ok=False), FakeLibrary([]), fake_insights())
    assert d["quiet"] and "All quiet" in d["message"] and d["severity"] == "HIGH" and "CHAIN BROKEN" in d["message"]


class Notifier:
    def __init__(self):
        self.sent = []

    def send_alert(self, title, message, severity):
        self.sent.append(title)
        return True


class Events:
    def __init__(self):
        self.logged = []

    def log(self, *args, **kwargs):
        self.logged.append(args)


def test_sends_once_a_day_after_the_set_time(tmp_path):
    sched = DigestScheduler(tmp_path / "state.json")
    cfg = DigestSettings(enabled=True, time="08:00")
    notifier, events = Notifier(), Events()
    digest = lambda now: {"title": "t", "message": "m", "severity": "INFO", "quiet": False}  # noqa: E731
    day = datetime.now().astimezone().replace(hour=7, minute=59, second=0, microsecond=0)
    assert not sched.check(cfg, notifier, events, day, digest)
    assert sched.check(cfg, notifier, events, day + timedelta(minutes=2), digest)
    assert not sched.check(cfg, notifier, events, day + timedelta(minutes=30), digest), "only once"
    assert not sched.check(cfg, notifier, events, day + timedelta(days=1, hours=8), digest), "too late: 16:00"
    assert sched.check(cfg, notifier, events, day + timedelta(days=2, minutes=5), digest)
    assert len(notifier.sent) == 2 and events.logged
    assert not DigestScheduler(tmp_path / "x.json").due(DigestSettings(enabled=False), day + timedelta(minutes=5))


def test_skip_quiet_days(tmp_path):
    sched = DigestScheduler(tmp_path / "state.json")
    cfg = DigestSettings(enabled=True, time="00:00", skip_quiet=True)
    notifier = Notifier()
    quiet = lambda now: {"title": "t", "message": "m", "severity": "INFO", "quiet": True}  # noqa: E731
    assert not sched.check(cfg, notifier, Events(), datetime.now().astimezone().replace(hour=1), quiet)
    assert notifier.sent == []
