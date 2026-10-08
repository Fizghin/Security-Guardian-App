import json

import pytest

from services.settings_service import SettingsError, SettingsService


def test_env_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SOURCE", "rtsp://cam.local/stream")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8080/v1")
    s = SettingsService(tmp_path / "settings.json").get()
    assert [c.source for c in s.cameras] == ["rtsp://cam.local/stream"]
    assert s.ai.provider == "openai"
    assert s.ai.base_url == "http://localhost:8080/v1"


def test_single_camera_settings_are_migrated(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"camera": {"source": "1", "name": "Porch"}, "ai": {"humor": 70}}))
    s = SettingsService(path).get()
    assert [(c.id, c.name, c.source) for c in s.cameras] == [("cam1", "Porch", "1")]
    assert s.ai.humor == 70


def test_overrides_persist_and_win_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SOURCE", "0")
    path = tmp_path / "settings.json"
    SettingsService(path).update({"ai": {"humor": 70}})
    assert json.loads(path.read_text()) == {"ai": {"humor": 70}}
    reloaded = SettingsService(path).get()
    assert reloaded.ai.humor == 70
    assert reloaded.ai.intimidation == 50  # untouched fields keep their defaults


def test_camera_add_update_remove(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    phone = svc.add_camera("Front door", "phone")
    assert phone.id == "front-door" and phone.is_phone and len(phone.token) > 16
    assert phone.audio == "device", "phones speak through their own speaker by default"
    again = svc.add_camera("Front door", "1")
    assert again.id == "front-door-2" and again.audio == "server"
    svc.update_camera("front-door", {"name": "Porch", "audio": "both"})
    reloaded = SettingsService(tmp_path / "settings.json").get()
    porch = reloaded.camera("front-door")
    assert (porch.name, porch.audio, porch.token) == ("Porch", "both", phone.token), "token persists"
    svc.remove_camera("front-door-2")
    assert [c.id for c in svc.get().cameras] == ["cam1", "front-door"]
    with pytest.raises(KeyError):
        svc.remove_camera("nope")


def test_non_phone_cameras_cannot_use_device_audio(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    cam = svc.update_camera("cam1", {"audio": "device"})
    assert cam.audio == "server"


def test_invalid_update_is_rejected_and_not_saved(tmp_path):
    svc = SettingsService(tmp_path / "settings.json")
    with pytest.raises(SettingsError, match="confidence"):
        svc.update({"detection": {"confidence": 5}})
    with pytest.raises(SettingsError, match="must increase"):
        svc.update({"escalation": {"level2_after": 30}})
    with pytest.raises(SettingsError, match="unique"):
        svc.update({"cameras": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]})
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
    svc.on_change(lambda old, new: seen.append((len(old.cameras), len(new.cameras))))
    svc.add_camera("Garage", "2")
    assert seen == [(1, 2)]


def test_phone_camera_from_env_keeps_its_pairing_token(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SOURCE", "phone")
    path = tmp_path / "settings.json"
    first = SettingsService(path).get().cameras[0]
    assert first.is_phone and first.token and first.audio == "device"
    assert SettingsService(path).get().cameras[0].token == first.token, "a restart must not break the pairing link"
