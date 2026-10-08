import threading
import time

import httpx
import pytest

from data.fallback_messages import LINES, get_fallback_message
from services import ai_service as ai_module
from services.ai_service import (AIError, AIService, WarningContext, build_system_prompt, build_user_message,
                                 check_reply, clean_response)
from services.settings_service import AISettings, SettingsService


@pytest.mark.parametrize("raw, expected", [
    ('"Please leave the property now."', "Please leave the property now."),
    ("<think>the user wants a warning</think>\nYou are being recorded.", "You are being recorded."),
    ("Guardian: Step back from the door.", "Step back from the door."),
    ("**Leave now.** You are on camera. This is your last warning. Go.", "Leave now. You are on camera."),
])
def test_clean_response(raw, expected):
    assert clean_response(raw) == expected


L2 = WarningContext(level=2, recording=True)


@pytest.mark.parametrize("text, ctx, problem", [
    ("This is private property. Please leave now.", L2, None),
    ("The police are on their way. Leave now.", L2, "police"),
    ("I am Officer Thompson. Leave the area.", L2, "officer"),
    ("Continue to stay, you will be removed by our staff.", L2, "removed"),
    ("Leave now, our team is coming.", L2, "our team"),
    ("The authorities have been told. Go away.", L2, "authorities"),
    ("Your neighbours are watching you. Leave now.", L2, "neighbours"),
    ("This is Sarah from security, please leave.", L2, "gives itself a name"),
    ("I am the security system. Leave now please.", L2, "introduces itself"),
    ("I cannot provide a response that includes personal information.", L2, "refusal"),
    ("You are being recorded, leave now.", WarningContext(level=1), "claims recording"),
    ("You are not being recorded right now.", L2, "not recording"),
    ("The owner has been notified. Leave now.", L2, "claims the owner was alerted"),
    ("The owner has been notified. Leave now.", WarningContext(level=3, alerted=True), None),
    ("The alarm is sounding, leave now.", L2, "alarm"),
    ("Leave now or the alarm will sound.", WarningContext(level=3, siren_next=True), None),
    ("Please leave now. | Who are you?", L2, "formatting"),
    ("You have just arrived.", WarningContext(level=1), "restates the facts"),
    ("Leave the property now please.", WarningContext(level=2, said=["Please leave the property now."]), "repeats"),
    ("Go.", L2, "too short"),
])
def test_check_reply(text, ctx, problem):
    result = check_reply(text, ctx)
    if problem is None:
        assert result is None
    else:
        assert result is not None and problem.lower() in result.lower()


def test_prompt_lists_only_true_facts():
    msg = build_user_message(WarningContext(level=3, people=2, location="Garage", recording=True, alerted=False,
                                            siren_next=True, said=["Hello there."]))
    assert "Camera location: Garage" in msg and "People on camera: 2" in msg
    assert "Video is being recorded: yes" in msg and "The owner has been alerted: no" in msg
    assert "The siren will sound if they stay: yes" in msg
    assert "- Hello there." in msg and "|" not in msg
    generic = build_user_message(WarningContext(level=1), generic=True)
    assert "location" not in generic.lower() and "People on camera" not in generic


def test_personality_changes_prompt():
    calm = build_system_prompt(AISettings(intimidation=10, humor=0))
    stern = build_system_prompt(AISettings(intimidation=90, humor=80))
    assert "calm and polite" in calm
    assert "stern and intimidating" in stern and "sarcastic" in stern


def test_fallback_lines_are_always_true():
    for level, tones in LINES.items():
        for lines in tones.values():
            for text, needs in lines:
                ctx = WarningContext(level=level, recording="rec" in needs, alerted="alert" in needs,
                                     siren="siren" in needs, siren_next="siren_next" in needs)
                assert check_reply(text, ctx) is None, text
    for _ in range(50):  # with no facts available, only untagged lines may be used
        text = get_fallback_message(3, 90, 90, facts=set())
        assert all(text != t or not needs for tones in LINES[3].values() for t, needs in tones)


@pytest.fixture
def ai(tmp_path):
    return AIService(SettingsService(tmp_path / "settings.json"), cache_file=tmp_path / "cache.json")


def test_falls_back_when_server_is_down(ai):
    result = ai.generate_warning(WarningContext(level=3, recording=True))
    assert result["source"] == "fallback"
    assert check_reply(result["text"], WarningContext(level=3, recording=True)) is None
    assert "Cannot reach" in result["error"]


def _fake_ollama(monkeypatch, models, replies=("Please identify yourself.",)):
    seen = {"posts": []}
    replies = list(replies)

    def get(url, **kw):
        return httpx.Response(200, json={"models": [{"name": m} for m in models]}, request=httpx.Request("GET", url))

    def post(url, json=None, **kw):
        seen["posts"].append(json)
        text = replies.pop(0) if len(replies) > 1 else replies[0]
        return httpx.Response(200, json={"message": {"content": text}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_module.httpx, "get", get)
    monkeypatch.setattr(ai_module.httpx, "post", post)
    return seen


def test_uses_first_installed_model_when_none_configured(ai, monkeypatch):
    seen = _fake_ollama(monkeypatch, ["qwen2.5:3b", "llama3.2:3b"])
    result = ai.generate_warning(WarningContext(level=1))
    assert result["source"] == "llm" and result["model"] == "llama3.2:3b"
    assert seen["posts"][0]["messages"][0]["role"] == "system"


def test_bad_reply_is_retried_then_replaced(ai, monkeypatch):
    seen = _fake_ollama(monkeypatch, ["m"], ["Police are coming!", "Please leave the property now."])
    result = ai.generate_warning(WarningContext(level=2))
    assert result["source"] == "llm" and result["text"] == "Please leave the property now."
    assert "not acceptable" in seen["posts"][1]["messages"][-1]["content"]

    _fake_ollama(monkeypatch, ["m"], ["Police are coming!"])
    result = ai.generate_warning(WarningContext(level=2))
    assert result["source"] == "fallback" and "rejected" in result["error"]
    assert ai.rejected == 3


def test_configured_model_must_exist(ai, monkeypatch):
    _fake_ollama(monkeypatch, ["llama3.2:3b"])
    ai.settings.update({"ai": {"model": "mistral"}})
    with pytest.raises(AIError, match="not installed"):
        ai.resolve_model(ai.settings.get().ai)
    ai.settings.update({"ai": {"model": "llama3.2"}})
    _fake_ollama(monkeypatch, ["llama3.2:latest"])
    assert ai.resolve_model(ai.settings.get().ai) == "llama3.2:latest"


def test_no_models_installed(ai, monkeypatch):
    _fake_ollama(monkeypatch, [])
    with pytest.raises(AIError, match="ollama pull"):
        ai.resolve_model(ai.settings.get().ai)


def test_prefetch_fills_cache_and_survives_restart(ai, monkeypatch, tmp_path):
    lines = iter(["Hello, who are you here to see?", "Good evening, can I help you?", "Please tell me why you are here."])
    _fake_ollama(monkeypatch, ["m"])
    monkeypatch.setattr(ai_module.httpx, "post", lambda url, json=None, **kw: httpx.Response(
        200, json={"message": {"content": next(lines)}}, request=httpx.Request("POST", url)))
    ctxs = [WarningContext(level=1)]
    for _ in range(2):
        assert ai.prefetch(ctxs)
        for _ in range(100):
            if not ai._prefetching:
                break
            time.sleep(0.02)
    assert ai.cached_count() == 2
    assert ai.prefetch(ctxs) is False, "cache already full"

    reloaded = AIService(ai.settings, cache_file=tmp_path / "cache.json")
    assert reloaded.cached_count() == 2
    assert reloaded.take_cached(WarningContext(level=1)) == "Hello, who are you here to see?"
    assert reloaded.take_cached(WarningContext(level=2)) is None, "different situation, different key"

    ai.settings.update({"ai": {"intimidation": 95}})
    assert ai.cached_count() == 0, "a new tone invalidates prepared lines"


def test_requests_are_one_per_camera(ai):
    ai._pending.add("door")
    assert ai.request_warning(lambda r: None, WarningContext(level=1), "door") is False
    ai._pending.clear()


def test_openai_compatible_server(ai, monkeypatch):
    calls = {}

    def get(url, headers=None, **kw):
        calls["models_url"], calls["auth"] = url, headers
        return httpx.Response(200, json={"data": [{"id": "local-model"}]}, request=httpx.Request("GET", url))

    def post(url, headers=None, json=None, **kw):
        calls["chat_url"] = url
        return httpx.Response(200, json={"choices": [{"message": {"content": "You are on camera. Please leave."}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_module.httpx, "get", get)
    monkeypatch.setattr(ai_module.httpx, "post", post)
    ai.settings.update({"ai": {"provider": "openai", "base_url": "http://localhost:1234/v1/", "api_key": "k"}})
    result = ai.generate_warning(WarningContext(level=2))
    assert (result["source"], result["model"]) == ("llm", "local-model")
    assert calls["models_url"] == "http://localhost:1234/v1/models"
    assert calls["chat_url"] == "http://localhost:1234/v1/chat/completions"
    assert calls["auth"] == {"Authorization": "Bearer k"}


def _wait(cond, seconds=2.0):
    end = time.time() + seconds
    while not cond() and time.time() < end:
        time.sleep(0.01)
    return cond()


def test_warm_up_loads_without_a_short_timeout(ai, monkeypatch):
    seen = _fake_ollama(monkeypatch, ["m"])
    gate = threading.Event()
    calls = []

    def post(url, json=None, timeout=None, **kw):
        calls.append((url, json, timeout))
        gate.wait(2)  # a slow disk: the load is still running
        return httpx.Response(200, json={"done": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_module.httpx, "post", post)
    assert ai.warm_up(quiet=True)
    assert _wait(lambda: calls)
    assert ai.status()["loading"] is True
    assert ai.warm_up() is False, "one load at a time"
    assert ai.prefetch([WarningContext(level=1)]) is False, "nothing else is queued behind the load"

    url, body, timeout = calls[0]
    assert url.endswith("/api/generate") and body == {"model": "m", "keep_alive": ai_module.KEEP_ALIVE}
    assert timeout.read >= 300, "giving up early makes Ollama abandon the load"

    ai.last_error = "timed out"
    gate.set()
    assert _wait(lambda: not ai.loading)
    assert ai.last_error is None and ai.status()["model"] == "m"
    assert not seen["posts"], "no chat request was needed"


def test_keep_warm_only_refreshes_now_and_then(ai, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "warm_up", lambda quiet=False: calls.append(quiet))
    ai._warm_attempt = time.time()
    ai.keep_warm()
    assert calls == []
    ai._warm_attempt = time.time() - ai_module.REWARM_SECONDS
    ai.keep_warm()
    assert calls == [True]


def test_failed_load_is_reported(ai):
    assert ai.warm_up(quiet=True)
    assert _wait(lambda: not ai.loading)
    assert "Cannot reach" in ai.status()["last_error"]
