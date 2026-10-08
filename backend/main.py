import asyncio
import contextlib
import socket
from contextlib import asynccontextmanager

from config import FRONTEND_DIST, PHONE_PORT, migrate_legacy_data

migrate_legacy_data()

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402

from api import phone_router, router, ws_router  # noqa: E402
from models.database import init_db  # noqa: E402
from services.ai_service import ai_service  # noqa: E402
from services.camera_service import camera_manager  # noqa: E402
from services.event_service import event_service  # noqa: E402
from services.face_service import face_service  # noqa: E402
from services.phone_service import ensure_certificate  # noqa: E402
from services.recording_service import recording_library  # noqa: E402
from services.settings_service import Settings, settings_service  # noqa: E402
from services.siren_service import siren_service  # noqa: E402
from services.tts_service import tts_service  # noqa: E402

PHONE_PATHS = ("/phone", "/api/phone/")


def apply_settings(old: Settings | None, new: Settings) -> None:
    camera_manager.apply(old, new)
    if new.detection.face_recognition and (old is None or not old.detection.face_recognition):
        face_service.prepare_async()
    model_fields = ("provider", "base_url", "model", "api_key")
    if old is not None and any(getattr(old.ai, f) != getattr(new.ai, f) for f in model_fields):
        ai_service.warm_up()
    if old is not None and old.recording.retention_days != new.recording.retention_days:
        recording_library.prune(new.recording.retention_days)


class _EmbeddedServer(uvicorn.Server):
    """Second listener inside this process; the main server owns Ctrl+C handling."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


async def _run_phone_listener(server: uvicorn.Server) -> None:
    try:
        await server.serve()
    except SystemExit:  # uvicorn exits when it cannot bind; keep the dashboard running
        print(f"[phone] Could not open port {PHONE_PORT} for phone cameras")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cfg = settings_service.get()
    removed = recording_library.prune(cfg.recording.retention_days)
    if removed:
        event_service.log("SYSTEM", f"Deleted {removed} recording(s) older than {cfg.recording.retention_days} days")
    camera_manager.start()
    if cfg.detection.face_recognition:
        face_service.prepare_async()
    settings_service.on_change(apply_settings)
    tts_service.check_async()
    ai_service.warm_up()

    phone_server, phone_task = None, None
    if PHONE_PORT > 0:
        if _port_free(PHONE_PORT):
            cert, key = ensure_certificate()
            phone_server = _EmbeddedServer(uvicorn.Config(app, host="0.0.0.0", port=PHONE_PORT, ssl_certfile=cert,
                                                          ssl_keyfile=key, lifespan="off", log_level="warning"))
            phone_task = asyncio.create_task(_run_phone_listener(phone_server))
        else:
            print(f"[phone] Port {PHONE_PORT} is in use; phone cameras need another PHONE_PORT in backend/.env")

    event_service.log("SYSTEM", f"Guardian started ({'armed' if cfg.armed else 'disarmed'}, "
                                f"{len([c for c in cfg.cameras if c.enabled])} camera(s))", "INFO")
    yield
    if phone_server:
        phone_server.should_exit = True
        await phone_task
    camera_manager.stop()
    siren_service.stop()
    event_service.log("SYSTEM", "Guardian stopped", "INFO")


class PhonePortGuard:
    """The phone port is open to the local network, so it only serves the phone camera page."""

    def __init__(self, app, port: int):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        server = scope.get("server")
        if (scope["type"] in ("http", "websocket") and self.port and server and server[1] == self.port
                and not scope["path"].startswith(PHONE_PATHS)):
            if scope["type"] == "http":
                await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)


app = FastAPI(title="Guardian", description="Local AI security cameras", version="2.1.0", lifespan=lifespan)

# The dashboard is normally served by this app (same origin). CORS is only for the
# Vite dev server, which proxies requests anyway.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:2500", "http://127.0.0.1:2500"],
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(PhonePortGuard, port=PHONE_PORT)
app.include_router(router)
app.include_router(ws_router)
app.include_router(phone_router)

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="dashboard")
else:
    @app.get("/")
    def no_dashboard():
        return {"message": "Guardian API is running. Build the dashboard with: cd frontend && npm run build",
                "docs": "/docs"}
