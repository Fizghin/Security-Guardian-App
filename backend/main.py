from contextlib import asynccontextmanager

from config import FRONTEND_DIST, migrate_legacy_data

migrate_legacy_data()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from api import router, ws_router  # noqa: E402
from models.database import init_db  # noqa: E402
from services.ai_service import ai_service  # noqa: E402
from services.event_service import event_service  # noqa: E402
from services.face_service import face_service  # noqa: E402
from services.pipeline_service import pipeline_service  # noqa: E402
from services.recording_service import recording_service  # noqa: E402
from services.settings_service import Settings, settings_service  # noqa: E402
from services.siren_service import siren_service  # noqa: E402
from services.tts_service import tts_service  # noqa: E402
from services.video_service import video_service  # noqa: E402


def apply_settings(old: Settings | None, new: Settings) -> None:
    rec = new.recording
    recording_service.configure(rec.preroll_seconds, rec.postroll_seconds, rec.max_clip_seconds)
    if old is None or old.camera.source != new.camera.source:
        video_service.start(new.camera.source)
    if new.detection.face_recognition and (old is None or not old.detection.face_recognition):
        face_service.prepare_async()
    model_fields = ("provider", "base_url", "model", "api_key")
    if old is not None and any(getattr(old.ai, f) != getattr(new.ai, f) for f in model_fields):
        ai_service.warm_up()
    if old is not None and old.recording.retention_days != rec.retention_days:
        recording_service.prune(rec.retention_days)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cfg = settings_service.get()
    apply_settings(None, cfg)
    settings_service.on_change(apply_settings)
    removed = recording_service.prune(cfg.recording.retention_days)
    if removed:
        event_service.log("SYSTEM", f"Deleted {removed} recording(s) older than {cfg.recording.retention_days} days")
    pipeline_service.start()
    tts_service.check_async()
    ai_service.warm_up()
    event_service.log("SYSTEM", f"Guardian started ({'armed' if cfg.armed else 'disarmed'})", "INFO")
    yield
    pipeline_service.stop()
    recording_service.shutdown()
    siren_service.stop()
    video_service.stop()
    event_service.log("SYSTEM", "Guardian stopped", "INFO")


app = FastAPI(title="Guardian", description="Local AI security camera", version="2.0.0", lifespan=lifespan)

# The dashboard is normally served by this app (same origin). CORS is only for the
# Vite dev server, which proxies requests anyway.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:2500", "http://127.0.0.1:2500"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(ws_router)

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="dashboard")
else:
    @app.get("/")
    def no_dashboard():
        return {"message": "Guardian API is running. Build the dashboard with: cd frontend && npm run build",
                "docs": "/docs"}
