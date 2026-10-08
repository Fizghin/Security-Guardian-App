import httpx
import pytest

from services import ai_service as ai_module
from services.ai_service import AIError, AIService, build_system_prompt, clean_response
from services.settings_service import AISettings, SettingsService


@pytest.mark.parametrize("raw, expected", [
    ('"Please leave the property now."', "Please leave the property now."),
    ("<think>the user wants a warning</think>\nYou are being recorded.", "You are being recorded."),
    ("Guardian: Step back from the door.", "Step back from the door."),
    ("**Leave now.** You are on camera. This is your last warning. Go.", "Leave now. You are on camera."),
])
def test_clean_response(raw, expected):
    assert clean_response(raw) == expected


def test_personality_changes_prompt():
    calm = build_system_prompt(AISettings(intimidation=10, humor=0))
    stern = build_system_prompt(AISettings(intimidation=90, humor=80))
    assert "calm and polite" in calm
    assert "stern and intimidating" in stern and "sarcastic" in stern


@pytest.fixture
def ai(tmp_path):
    return AIService(SettingsService(tmp_path / "settings.json"))


def test_falls_back_when_server_is_down(ai):
    result = ai.generate_warning(level=3, seconds=12)
    assert result["source"] == "fallback"
    assert result["text"]
    assert "Cannot reach" in result["error"]


def _fake_ollama(monkeypatch, models, reply="Please identify yourself."):
    seen = {}

    def get(url, **kw):
        return httpx.Response(200, json={"models": [{"name": m} for m in models]}, request=httpx.Request("GET", url))

    def post(url, json=None, **kw):
        seen["payload"] = json
        return httpx.Response(200, json={"message": {"content": reply}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_module.httpx, "get", get)
    monkeypatch.setattr(ai_module.httpx, "post", post)
    return seen


def test_uses_first_installed_model_when_none_configured(ai, monkeypatch):
    seen = _fake_ollama(monkeypatch, ["qwen2.5:3b", "llama3.2:3b"])
    result = ai.generate_warning(level=1)
    assert result["source"] == "llm"
    assert result["model"] == "llama3.2:3b"  # sorted list, first entry
    assert seen["payload"]["model"] == "llama3.2:3b"
    assert seen["payload"]["messages"][0]["role"] == "system"


def test_configured_model_must_exist(ai, monkeypatch):
    _fake_ollama(monkeypatch, ["llama3.2:3b"])
    ai.settings.update({"ai": {"model": "mistral"}})
    with pytest.raises(AIError, match="not installed"):
        ai.resolve_model(ai.settings.get().ai)
    ai.settings.update({"ai": {"model": "llama3.2"}})  # ":latest" style short names resolve
    _fake_ollama(monkeypatch, ["llama3.2:latest"])
    assert ai.resolve_model(ai.settings.get().ai) == "llama3.2:latest"


def test_no_models_installed(ai, monkeypatch):
    _fake_ollama(monkeypatch, [])
    with pytest.raises(AIError, match="ollama pull"):
        ai.resolve_model(ai.settings.get().ai)


def test_openai_compatible_server(ai, monkeypatch):
    calls = {}

    def get(url, headers=None, **kw):
        calls["models_url"], calls["auth"] = url, headers
        return httpx.Response(200, json={"data": [{"id": "local-model"}]}, request=httpx.Request("GET", url))

    def post(url, headers=None, json=None, **kw):
        calls["chat_url"] = url
        return httpx.Response(200, json={"choices": [{"message": {"content": "You are on camera."}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_module.httpx, "get", get)
    monkeypatch.setattr(ai_module.httpx, "post", post)
    ai.settings.update({"ai": {"provider": "openai", "base_url": "http://localhost:1234/v1/", "api_key": "k"}})
    result = ai.generate_warning(level=2)
    assert result == {**result, "source": "llm", "text": "You are on camera.", "model": "local-model"}
    assert calls["models_url"] == "http://localhost:1234/v1/models"
    assert calls["chat_url"] == "http://localhost:1234/v1/chat/completions"
    assert calls["auth"] == {"Authorization": "Bearer k"}
