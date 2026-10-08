"""
Runtime settings.

Defaults come from environment variables (.env). Anything changed from the
dashboard is stored as an override in storage/settings.json, so .env keeps
applying to fields that were never edited in the UI.
"""
import copy
import json
import os
import threading
from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from config import SETTINGS_FILE, env_str

OLLAMA_DEFAULT_URL = "http://localhost:11434"
OPENAI_COMPAT_DEFAULT_URL = "http://localhost:1234/v1"  # LM Studio's default


class CameraSettings(BaseModel):
    # "auto", a device index ("0"), an RTSP/HTTP URL, a video file path, or "none".
    source: str = "auto"
    name: str = "Camera 1"


class DetectionSettings(BaseModel):
    confidence: float = Field(0.5, ge=0.1, le=0.95)
    min_person_height: int = Field(10, ge=0, le=90, description="% of frame height")
    interval_ms: int = Field(400, ge=100, le=5000)
    face_recognition: bool = True
    face_match_threshold: float = Field(0.36, ge=0.2, le=0.8)
    insider_grace_seconds: int = Field(20, ge=0, le=300)


class EscalationSettings(BaseModel):
    level2_after: int = Field(5, ge=1, le=600)
    level3_after: int = Field(10, ge=2, le=1200)
    level4_after: int = Field(15, ge=3, le=1800)
    clear_after: int = Field(10, ge=2, le=600)
    record_at_level: int = Field(2, ge=1, le=4)
    alert_at_level: int = Field(3, ge=1, le=4)
    siren_enabled: bool = True
    siren_at_level: int = Field(4, ge=1, le=4)
    siren_max_seconds: int = Field(60, ge=5, le=600)

    @model_validator(mode="after")
    def _ordered(self):
        if not (self.level2_after < self.level3_after < self.level4_after):
            raise ValueError("Escalation times must increase: level 2 < level 3 < level 4")
        return self


class RecordingSettings(BaseModel):
    preroll_seconds: int = Field(5, ge=0, le=15)
    postroll_seconds: int = Field(8, ge=0, le=60)
    max_clip_seconds: int = Field(300, ge=30, le=1800)
    retention_days: int = Field(30, ge=0, le=3650, description="0 = keep forever")


class AISettings(BaseModel):
    provider: Literal["ollama", "openai"] = "ollama"
    base_url: str = OLLAMA_DEFAULT_URL
    model: str = ""  # empty = use the first model installed on the server
    api_key: str = ""
    timeout_seconds: int = Field(30, ge=5, le=180)
    intimidation: int = Field(50, ge=0, le=100)
    humor: int = Field(20, ge=0, le=100)
    persistence: int = Field(60, ge=0, le=100)
    voice_enabled: bool = True
    voice_rate: int = Field(165, ge=80, le=300)


class Settings(BaseModel):
    armed: bool = True
    camera: CameraSettings = CameraSettings()
    detection: DetectionSettings = DetectionSettings()
    escalation: EscalationSettings = EscalationSettings()
    recording: RecordingSettings = RecordingSettings()
    ai: AISettings = AISettings()


def _env_defaults() -> dict:
    provider = env_str("AI_PROVIDER", "ollama").lower()
    if provider not in ("ollama", "openai"):
        provider = "ollama"
    if provider == "ollama":
        base_url = env_str("OLLAMA_BASE_URL", OLLAMA_DEFAULT_URL)
        model = env_str("OLLAMA_MODEL")
    else:
        base_url = env_str("OPENAI_BASE_URL", OPENAI_COMPAT_DEFAULT_URL)
        model = env_str("OPENAI_MODEL")
    return {
        "camera": {"source": env_str("VIDEO_SOURCE", "auto")},
        "ai": {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "api_key": env_str("OPENAI_API_KEY"),
        },
    }


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class SettingsError(ValueError):
    pass


class SettingsService:
    def __init__(self, path=SETTINGS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Settings, Settings], None]] = []
        self._overrides = self._load_overrides()
        try:
            self._settings = self._build(self._overrides)
        except SettingsError as exc:
            print(f"[settings] Ignoring invalid {self.path.name}: {exc}")
            self._overrides = {}
            self._settings = self._build({})

    def _load_overrides(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[settings] Could not read {self.path}: {exc}")
            return {}

    @staticmethod
    def _build(overrides: dict) -> Settings:
        merged = deep_merge(Settings().model_dump(), _env_defaults())
        merged = deep_merge(merged, overrides)
        try:
            return Settings.model_validate(merged)
        except ValidationError as exc:
            msgs = []
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                msgs.append(f"{loc}: {err['msg']}" if loc else err["msg"])
            raise SettingsError("; ".join(msgs)) from exc

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def update(self, patch: dict) -> Settings:
        with self._lock:
            overrides = deep_merge(self._overrides, patch)
            new = self._build(overrides)
            old = self._settings
            self._overrides = overrides
            self._settings = new
            self._save()
        for listener in list(self._listeners):
            try:
                listener(old, new)
            except Exception as exc:  # a broken listener must not block saving
                print(f"[settings] Listener error: {exc}")
        return new

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._overrides, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def on_change(self, listener: Callable[[Settings, Settings], None]) -> None:
        self._listeners.append(listener)

    def public(self) -> dict:
        """Settings as sent to the dashboard (API key is never echoed back)."""
        data = self.get().model_dump()
        data["ai"]["api_key_set"] = bool(data["ai"].pop("api_key"))
        return data


settings_service = SettingsService()
