"""
Runtime settings.

Defaults come from environment variables (.env). Anything changed from the
dashboard is stored as an override in storage/settings.json, so .env keeps
applying to fields that were never edited in the UI.
"""
import copy
import json
import os
import re
import secrets
import threading
from typing import Callable, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from config import SETTINGS_FILE, env_str
from services.zones import clean_zone

OLLAMA_DEFAULT_URL = "http://localhost:11434"
OPENAI_COMPAT_DEFAULT_URL = "http://localhost:1234/v1"  # LM Studio's default
PHONE_SOURCE = "phone"


def new_token() -> str:
    return secrets.token_urlsafe(18)


class CameraConfig(BaseModel):
    id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    name: str = Field("Camera", min_length=1, max_length=40)
    # "auto", a device index ("0"), an RTSP/HTTP URL, a video file path, "phone" or "none".
    source: str = "auto"
    enabled: bool = True
    # Phone cameras authenticate with this secret (it is part of the pairing link).
    token: str = ""
    # Where warnings and the siren play: this computer, the phone itself, or both.
    audio: Literal["server", "device", "both"] = "server"
    # Areas where people count: polygons of [x, y] corners in 0..1. Empty = the whole picture.
    zones: list[list[list[float]]] = Field(default_factory=list)
    # The picture's width / height when the zones were drawn, to notice when it changes shape.
    zones_aspect: float | None = Field(None, ge=0.1, le=10)

    @field_validator("zones")
    @classmethod
    def _zones(cls, zones: list[list[list[float]]]):
        if len(zones) > 8:
            raise ValueError("At most 8 zones per camera")
        return [clean_zone(polygon) for polygon in zones]

    @property
    def is_phone(self) -> bool:
        return self.source == PHONE_SOURCE

    @model_validator(mode="after")
    def _defaults(self):
        if self.is_phone and not self.token:
            self.token = new_token()
        if not self.is_phone and self.audio != "server":
            self.audio = "server"  # only phones have a speaker Guardian can reach
        if not self.zones:
            self.zones_aspect = None
        return self


class DetectionSettings(BaseModel):
    confidence: float = Field(0.5, ge=0.1, le=0.95)
    min_person_height: int = Field(10, ge=0, le=90, description="% of frame height")
    interval_ms: int = Field(400, ge=100, le=5000)
    face_recognition: bool = True
    face_match_threshold: float = Field(0.36, ge=0.2, le=0.8)
    # How long a newly seen person may stay unidentified before being treated as a stranger.
    identify_seconds: float = Field(2.0, ge=0, le=10)
    insider_grace_seconds: int = Field(20, ge=0, le=300)
    # Loud sounds heard by phone cameras: logged with a picture, or also recorded and alerted while armed.
    sound_alerts: Literal["off", "log", "alert"] = "log"
    sound_sensitivity: int = Field(5, ge=1, le=10)
    # Repeat visitors: remember strangers' faces while armed and recognise them when they return.
    remember_visitors: bool = True
    visitor_retention_days: int = Field(90, ge=0, le=3650, description="0 = keep forever")
    # A sighting this long after the visitor was last seen counts as a new visit.
    visit_gap_minutes: int = Field(30, ge=0, le=1440)
    # Draw where each person walked in the last few seconds on the live picture and in clips.
    motion_trails: bool = True
    # Count where people walk on each camera's picture for the Insights page.
    activity_heatmap: bool = True


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
    offline_alert_seconds: int = Field(60, ge=0, le=3600, description="0 = never")

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
    greet_insiders: bool = False
    greet_cooldown_minutes: int = Field(60, ge=1, le=1440)


class PhoneSettings(BaseModel):
    fps: int = Field(8, ge=1, le=15)
    max_width: int = Field(960, ge=320, le=1920)
    quality: float = Field(0.7, ge=0.3, le=0.95)


HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class ScheduleRule(BaseModel):
    days: list[int] = Field(default_factory=lambda: list(range(7)))  # 0 = Monday
    start: str = Field("22:00", pattern=HHMM)
    end: str = Field("07:00", pattern=HHMM)  # earlier than start: runs past midnight

    @field_validator("days")
    @classmethod
    def _days(cls, days: list[int]):
        days = sorted(set(days))
        if not days or days[0] < 0 or days[-1] > 6:
            raise ValueError("Pick at least one day")
        return days

    @model_validator(mode="after")
    def _span(self):
        if self.start == self.end:
            raise ValueError("A period needs different start and end times")
        return self


class ScheduleSettings(BaseModel):
    enabled: bool = False
    rules: list[ScheduleRule] = Field(default_factory=list, max_length=14)


CHANNELS = ("discord", "telegram", "ntfy", "webhook", "email")
# Address fields and the schemes they accept. Discord only ever hands out https:// webhooks.
URL_FIELDS = {"discord_webhook": ("https",), "ntfy_url": ("https", "http"), "webhook_url": ("https", "http")}


def url_problem(field: str, value: str) -> str | None:
    """Why `value` is not usable for an address field, or None. Settings, .env and saved files all use this."""
    if not value:
        return None
    schemes = URL_FIELDS[field]
    try:
        parts = urlsplit(value)
        ok = parts.scheme in schemes and bool(parts.hostname)
    except ValueError:
        ok = False
    return None if ok else f"must be a web address starting with {' or '.join(f'{s}://' for s in schemes)}"


class NotificationSettings(BaseModel):
    discord_webhook: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    ntfy_url: str = ""  # topic URL, e.g. https://ntfy.sh/some-secret-topic
    ntfy_token: str = ""
    webhook_url: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    email_to: str = ""

    # Never sent to the dashboard; it only learns whether they are set
    SECRETS: ClassVar[tuple[str, ...]] = ("discord_webhook", "telegram_token", "ntfy_token", "smtp_password", "webhook_url")

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator(*URL_FIELDS)
    @classmethod
    def _url(cls, value: str, info):
        problem = url_problem(info.field_name, value)
        if problem:
            raise ValueError(problem)
        return value

    def configured(self) -> list[str]:
        """Channels that have everything they need. The notifier and the dashboard both go by this."""
        ready = {
            "discord": bool(self.discord_webhook),
            "telegram": bool(self.telegram_token and self.telegram_chat_id),
            "ntfy": bool(self.ntfy_url),
            "webhook": bool(self.webhook_url),
            "email": bool(self.smtp_user and self.smtp_password and self.email_to),
        }
        return [c for c in CHANNELS if ready[c]]


class Settings(BaseModel):
    armed: bool = True
    cameras: list[CameraConfig] = [CameraConfig(id="cam1", name="Camera 1")]
    detection: DetectionSettings = DetectionSettings()
    escalation: EscalationSettings = EscalationSettings()
    recording: RecordingSettings = RecordingSettings()
    ai: AISettings = AISettings()
    phone: PhoneSettings = PhoneSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    notifications: NotificationSettings = NotificationSettings()

    @field_validator("cameras")
    @classmethod
    def _unique_ids(cls, cams: list[CameraConfig]):
        ids = [c.id for c in cams]
        if len(ids) != len(set(ids)):
            raise ValueError("Camera ids must be unique")
        if len(cams) > 16:
            raise ValueError("At most 16 cameras are supported")
        return cams

    def camera(self, camera_id: str) -> CameraConfig | None:
        return next((c for c in self.cameras if c.id == camera_id), None)


def _env_url(field: str, *names: str) -> str:
    """An address from .env. An unusable one is ignored with a warning, so a typo never stops Guardian from starting."""
    for name in names:
        value = env_str(name)
        if value:
            problem = url_problem(field, value)
            if problem:
                print(f"[settings] Ignoring {name} in backend/.env: it {problem}")
                return ""
            return value
    return ""


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
    source = env_str("VIDEO_SOURCE", "auto")
    return {
        "cameras": [{"id": "cam1", "name": "Camera 1", "source": source,
                     "audio": "device" if source == PHONE_SOURCE else "server"}],
        "ai": {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "api_key": env_str("OPENAI_API_KEY"),
        },
        "notifications": {
            "discord_webhook": _env_url("discord_webhook", "DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK"),
            "telegram_token": env_str("TELEGRAM_BOT_TOKEN"),
            "telegram_chat_id": env_str("TELEGRAM_CHAT_ID"),
            "ntfy_url": _env_url("ntfy_url", "NTFY_URL"),
            "ntfy_token": env_str("NTFY_TOKEN"),
            "webhook_url": _env_url("webhook_url", "WEBHOOK_URL"),
            "smtp_host": env_str("SMTP_HOST", "smtp.gmail.com"),
            "smtp_port": int(env_str("SMTP_PORT", "587")) if env_str("SMTP_PORT", "587").isdigit() else 587,
            "smtp_user": env_str("SMTP_USER"),
            "smtp_password": env_str("SMTP_PASSWORD"),
            "email_to": env_str("ALERT_EMAIL"),
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


def _migrate(overrides: dict) -> dict:
    """Bring settings saved by older versions up to date."""
    # Earlier versions accepted addresses that are now rejected (e.g. http:// for Discord). Drop just
    # those, so one of them doesn't make Guardian ignore every other saved setting.
    notifications = overrides.get("notifications")
    if isinstance(notifications, dict):
        for field in URL_FIELDS:
            value = notifications.get(field)
            problem = url_problem(field, value.strip()) if isinstance(value, str) else None
            if problem:
                print(f"[settings] Ignoring notifications.{field} in settings.json: it {problem}")
                notifications.pop(field)
    # v2.0 had a single `camera`; turn it into the camera list.
    if "camera" in overrides:
        old = overrides.pop("camera") or {}
        if "cameras" not in overrides:
            cam = {"id": "cam1", "name": old.get("name") or "Camera 1"}
            if old.get("source"):
                cam["source"] = old["source"]
            if "source" not in cam:
                cam["source"] = env_str("VIDEO_SOURCE", "auto")
            overrides["cameras"] = [cam]
    # Zones saved before outlines that cross themselves or have no area were refused: drop them
    # rather than ignoring the whole file.
    cams = overrides.get("cameras")
    for cam in cams if isinstance(cams, list) else []:
        zones = cam.get("zones") if isinstance(cam, dict) else None
        if isinstance(zones, list):
            kept = []
            for zone in zones:
                try:
                    kept.append(clean_zone(zone))
                except (TypeError, ValueError) as exc:
                    print(f"[settings] Dropping a zone of camera {cam.get('id')}: {exc}")
            cam["zones"] = kept
    return overrides


class SettingsError(ValueError):
    pass


def slugify(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "cam"
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


class SettingsService:
    def __init__(self, path=SETTINGS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Settings, Settings], None]] = []
        self._env = _env_defaults()  # read once, so .env warnings print once
        self._overrides = _migrate(self._load_overrides())
        try:
            self._settings = self._build(self._overrides)
        except SettingsError as exc:
            print(f"[settings] Ignoring invalid {self.path.name}: {exc}")
            self._overrides = {}
            self._settings = self._build({})
        self._persist_generated()

    def _load_overrides(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[settings] Could not read {self.path}: {exc}")
            return {}

    def _persist_generated(self) -> None:
        """Phone tokens are generated during validation; save them so pairing links stay valid."""
        cams = self._overrides.get("cameras")
        stored = {c.get("id"): c.get("token") for c in cams if isinstance(c, dict)} if isinstance(cams, list) else {}
        # Also covers VIDEO_SOURCE=phone, where the camera comes from .env rather than settings.json
        if any(c.is_phone and stored.get(c.id) != c.token for c in self._settings.cameras):
            self._overrides["cameras"] = [c.model_dump() for c in self._settings.cameras]
            self._save()

    def _build(self, overrides: dict) -> Settings:
        merged = deep_merge(Settings().model_dump(), self._env)
        merged = deep_merge(merged, overrides)
        try:
            return Settings.model_validate(merged)
        except ValidationError as exc:
            msgs = []
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                msg = err["msg"].removeprefix("Value error, ")
                msgs.append(f"{loc}: {msg}" if loc else msg)
            raise SettingsError("; ".join(msgs)) from exc

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def update(self, patch: dict) -> Settings:
        with self._lock:
            overrides = deep_merge(self._overrides, patch)
            new = self._build(overrides)
            if "cameras" in patch:
                # Store the validated list so generated ids/tokens are persisted.
                overrides["cameras"] = [c.model_dump() for c in new.cameras]
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

    # ---- camera list helpers -------------------------------------------------
    def add_camera(self, name: str, source: str, audio: str | None = None) -> CameraConfig:
        with self._lock:
            cams = [c.model_dump() for c in self.get().cameras]
            cam = {"id": slugify(name, {c["id"] for c in cams}), "name": name.strip(), "source": source.strip()}
            if source == PHONE_SOURCE:
                cam["audio"] = audio or "device"
            new = self.update({"cameras": cams + [cam]})
            return new.cameras[-1]

    def update_camera(self, camera_id: str, changes: dict) -> CameraConfig:
        with self._lock:
            cams = [c.model_dump() for c in self.get().cameras]
            for c in cams:
                if c["id"] == camera_id:
                    c.update({k: v for k, v in changes.items() if k not in ("id",)})
                    break
            else:
                raise KeyError(camera_id)
            return self.update({"cameras": cams}).camera(camera_id)

    def remove_camera(self, camera_id: str) -> None:
        with self._lock:
            cams = [c.model_dump() for c in self.get().cameras if c.id != camera_id]
            if len(cams) == len(self.get().cameras):
                raise KeyError(camera_id)
            self.update({"cameras": cams})

    def on_change(self, listener: Callable[[Settings, Settings], None]) -> None:
        self._listeners.append(listener)

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._overrides, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def public(self) -> dict:
        """Settings as sent to the dashboard: secrets become *_set flags and are never echoed back."""
        settings = self.get()
        data = settings.model_dump()
        data["ai"]["api_key_set"] = bool(data["ai"].pop("api_key"))
        for key in NotificationSettings.SECRETS:
            data["notifications"][f"{key}_set"] = bool(data["notifications"].pop(key))
        data["notifications"]["configured"] = settings.notifications.configured()
        return data


settings_service = SettingsService()
