import json

import pytest

from services.settings_service import SettingsError, SettingsService


def test_env_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SOURCE", "rtsp://cam.local/stream")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8080/v1")
    s = SettingsService(tmp_path / "settings.json").get()
    assert s.camera.source == "rtsp://cam.local/stream"
    assert s.ai.provider == "openai"
    assert s.ai.base_url == "http://localhost:8080/v1"


def test_overrides_persist_and_win_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SOURCE", "0")
    path = tmp_path / "settings.json"
    SettingsService(path).update({"camera": {"source": "1"}, "ai": {"humor": 70}})
    assert json.loads(path.read_text()) == {"camera": {"source": "1"}, "ai": {"humor": 70}}
    reloaded = SettingsService(path).get()
    assert reloaded.camera.source == "1"
    assert reloaded.ai.humor == 70
    assert reloaded.ai.intimidation == 50  # untouched fields keep their defaults


def test_invalid_update_is_rejected_and_not_saved(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    with pytest.raises(SettingsError, match="confidence"):
        svc.update({"detection": {"confidence": 5}})
    with pytest.raises(SettingsError, match="must increase"):
        svc.update({"escalation": {"level2_after": 30}})
    assert svc.get().detection.confidence == 0.5
    assert not (tmp_path / "settings.json").exists()


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"detection": {"confidence": "lots"}}))
    assert SettingsService(path).get().detection.confidence == 0.5


def test_api_key_is_never_exposed(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    svc.update({"ai": {"api_key": "secret"}})
    public = svc.public()
    assert "api_key" not in public["ai"]
    assert public["ai"]["api_key_set"] is True


def test_listeners_receive_old_and_new(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    seen = []
    svc.on_change(lambda old, new: seen.append((old.camera.source, new.camera.source)))
    svc.update({"camera": {"source": "2"}})
    assert seen[0][1] == "2"
