"""
Pre-written lines, used when the language model is unavailable or too slow.

Every line is tagged with the facts it relies on, so a line is only spoken
when it is true right now:
  rec        the camera is recording
  alert      the owner has actually been notified
  siren      the siren is sounding
  siren_next the siren will sound at the next level
"""
import random
from datetime import datetime

Line = tuple[str, frozenset]


def _l(text: str, *needs: str) -> Line:
    return text, frozenset(needs)


LINES: dict[int, dict[str, list[Line]]] = {
    1: {
        "calm": [
            _l("Hello. This is private property. Can I help you?"),
            _l("Hi there. You're on camera. Who are you here to see?"),
            _l("Good day. This area is monitored. How can I help you?"),
            _l("Hello. If you're making a delivery, please leave it at the door."),
        ],
        "firm": [
            _l("Hello. This is private property. Please state your business."),
            _l("You are on camera. Who are you here to see?"),
            _l("This area is monitored. Please identify yourself."),
            _l("Please stop there. This is private property. What do you need?"),
        ],
        "stern": [
            _l("Stop. This is private property and you are on camera. State your business."),
            _l("You are being watched. Identify yourself now."),
            _l("This is private property. Explain why you are here."),
        ],
        "witty": [
            _l("Hello! You've wandered into a monitored area. Lost, or just curious?"),
            _l("Hi there. The camera noticed you before you noticed it. Can I help?"),
        ],
    },
    2: {
        "calm": [
            _l("You've been here a while. This is private property, so please move along."),
            _l("Please leave the property unless someone is expecting you."),
            _l("You're being recorded. Please leave the property.", "rec"),
        ],
        "firm": [
            _l("You have been here too long. This is private property. Please leave."),
            _l("This visit is being recorded. Leave the property now.", "rec"),
            _l("You don't have permission to be here. Please go."),
        ],
        "stern": [
            _l("Leave the property now. You are not welcome here."),
            _l("You are being recorded. Leave immediately.", "rec"),
            _l("This is your warning. Step away and leave the property."),
        ],
        "witty": [
            _l("Still here? The camera is recording, so smile, then leave.", "rec"),
            _l("This isn't a waiting room. Please move along."),
        ],
    },
    3: {
        "calm": [
            _l("The owner has been notified. Please leave now.", "alert"),
            _l("You need to leave the property now."),
            _l("You are being recorded and the owner has been told. Please go.", "rec", "alert"),
        ],
        "firm": [
            _l("The owner has been alerted. Leave the property now.", "alert"),
            _l("Leave the property now. The alarm will sound if you stay.", "siren_next"),
            _l("You have been warned. Leave the property immediately."),
        ],
        "stern": [
            _l("The owner has your picture. Leave now.", "alert"),
            _l("Leave immediately. This is your last warning."),
            _l("You are on video and the owner has been alerted. Get off the property.", "rec", "alert"),
        ],
        "witty": [
            _l("Congratulations, you're on the owner's phone now. Time to leave.", "alert"),
            _l("Last chance to leave before this gets loud.", "siren_next"),
        ],
    },
    4: {
        "calm": [
            _l("The alarm is sounding. Please leave the property now.", "siren"),
            _l("You must leave the property immediately."),
        ],
        "firm": [
            _l("The alarm has been triggered. Leave immediately.", "siren"),
            _l("Everything is being recorded. Leave the property now.", "rec"),
            _l("Leave the property immediately."),
        ],
        "stern": [
            _l("Alarm activated. Leave now.", "siren"),
            _l("Get off this property immediately."),
            _l("You are on video. Leave right now.", "rec"),
        ],
        "witty": [
            _l("That noise is the alarm. It's your cue to leave.", "siren"),
        ],
    },
}

GREETINGS = ["Welcome back, {name}.", "Hi {name}.", "Hello {name}, good to see you.", "{time_greeting}, {name}."]


def tone_for(intimidation: int) -> str:
    return "stern" if intimidation >= 67 else "firm" if intimidation >= 34 else "calm"


def get_fallback_message(level: int, intimidation: int = 50, humor: int = 20,
                         facts: set[str] | frozenset = frozenset(), avoid: list[str] | None = None) -> str:
    level = min(4, max(1, level))
    tone = tone_for(intimidation)
    usable = lambda lines: [t for t, needs in lines if needs <= set(facts) and t not in (avoid or [])]  # noqa: E731
    pool = usable(LINES[level][tone])
    if humor >= 67 or (humor >= 34 and random.random() < 0.3):
        pool += usable(LINES[level]["witty"])
    if not pool:  # every tone has untagged lines, but they may all have been said already
        pool = [t for t, needs in LINES[level][tone] if needs <= set(facts)]
    return random.choice(pool)


def greeting(name: str, now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    time_greeting = "Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 18 else "Good evening"
    return random.choice(GREETINGS).format(name=name, time_greeting=time_greeting)
