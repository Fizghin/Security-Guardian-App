import asyncio
import base64
import contextlib
import hashlib
import hmac
import socket
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from config import DASHBOARD_PASSWORD, DEV_ORIGINS, FRONTEND_DIST, PHONE_PORT, migrate_legacy_data

migrate_legacy_data()

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse  # noqa: E402

from api import phone_router, router, ws_router  # noqa: E402
from evidence_api import router as evidence_router  # noqa: E402
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
from services.vault_service import evidence_vault  # noqa: E402
from services.visitor_service import visitor_service  # noqa: E402

def is_phone_path(path: str) -> bool:
    """The phone camera page and its API. Exact matches only: a prefix check would let
    "/phone/../index.html" through to the dashboard's static files."""
    return path == "/phone" or (path.startswith("/api/phone/") and ".." not in path.split("/"))


def apply_settings(old: Settings | None, new: Settings) -> None:
    camera_manager.apply(old, new)
    if new.detection.face_recognition and (old is None or not old.detection.face_recognition):
        face_service.prepare_async()
    model_fields = ("provider", "base_url", "model", "api_key")
    if old is not None and any(getattr(old.ai, f) != getattr(new.ai, f) for f in model_fields):
        ai_service.warm_up()
    if old is not None and old.recording.retention_days != new.recording.retention_days:
        recording_library.prune(new.recording.retention_days)
        event_service.prune_snapshots(new.recording.retention_days)
    if old is not None and old.detection.visitor_retention_days != new.detection.visitor_retention_days:
        visitor_service.prune()


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
    evidence_vault.start(recording_library, event_service)  # before anything is saved or deleted
    visitor_service.start()
    cfg = settings_service.get()
    removed = recording_library.prune(cfg.recording.retention_days)
    event_service.prune_snapshots(cfg.recording.retention_days)
    visitor_service.prune()
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
    evidence_vault.wait_idle()  # seal the clips the cameras just finished
    event_service.log("SYSTEM", "Guardian stopped", "INFO")


class PhonePortGuard:
    """The phone port is open to the local network, so it only serves the phone camera page."""

    def __init__(self, app, port: int):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        server = scope.get("server")
        if (scope["type"] in ("http", "websocket") and self.port and server and server[1] == self.port
                and not is_phone_path(scope["path"])):
            if scope["type"] == "http":
                await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Guardian · Sign in</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #09090b; color: #f4f4f5;
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  form { width: min(320px, calc(100vw - 32px)); display: grid; gap: 12px; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  input { padding: 10px 12px; border-radius: 6px; border: 1px solid #3f3f46; background: #18181b; color: inherit; font: inherit; }
  button { padding: 10px 12px; border-radius: 6px; border: 0; background: #2563eb; color: #fff; font: inherit; font-weight: 600; }
  p { margin: 0; color: #f87171; font-size: 14px; }
</style></head>
<body><form method="post" action="/login">
  <h1>Guardian</h1>
  <input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
  %ERROR%<button type="submit">Sign in</button>
</form></body></html>"""


class DashboardPassword:
    """Optional sign-in for the dashboard (DASHBOARD_PASSWORD). Browsers get a sign-in page that sets
    a session cookie, which also covers the WebSockets for live video, talk and listen. Scripts can send the password with
    HTTP Basic auth (any user name) instead."""

    COOKIE = "guardian_session"
    LOGIN_PATH = "/login"
    MAX_FORM = 4096

    @staticmethod
    def is_open(path: str) -> bool:
        return is_phone_path(path) or path == "/api/health"  # phones authenticate with their pairing link

    def __init__(self, app, password: str):
        self.app = app
        self.password = password
        self.session = hashlib.sha256(f"guardian-session:{password}".encode()).hexdigest() if password else ""

    def _matches(self, given: str) -> bool:
        return hmac.compare_digest(given.encode(), self.password.encode())

    def _authorised(self, headers: dict) -> bool:
        for part in headers.get(b"cookie", b"").decode("latin-1").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.COOKIE and hmac.compare_digest(value.encode("latin-1"), self.session.encode()):
                return True
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if auth[:6].lower() == "basic ":
            try:
                return self._matches(base64.b64decode(auth[6:]).decode("utf-8").partition(":")[2])
            except (ValueError, UnicodeDecodeError):
                return False
        return False

    async def _login(self, scope, receive, send) -> None:
        error = ""
        if scope["method"] == "POST":
            body = b""
            while len(body) <= self.MAX_FORM:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            given = parse_qs(body[:self.MAX_FORM].decode("utf-8", "replace")).get("password", [""])[0]
            if self._matches(given):
                response = RedirectResponse("/", status_code=303)
                response.set_cookie(self.COOKIE, self.session, max_age=30 * 86400, httponly=True, samesite="lax")
                await response(scope, receive, send)
                return
            await asyncio.sleep(1)  # slows down password guessing
            error = "<p>Wrong password.</p>"
        page = LOGIN_PAGE.replace("%ERROR%", error)
        await HTMLResponse(page, status_code=401 if error else 200)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if not self.password or scope["type"] not in ("http", "websocket") or self.is_open(scope["path"]):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope["path"] == self.LOGIN_PATH:
            await self._login(scope, receive, send)
            return
        headers = dict(scope["headers"])
        if self._authorised(headers):
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        elif scope["method"] == "GET" and b"text/html" in headers.get(b"accept", b""):
            await RedirectResponse(self.LOGIN_PATH, status_code=303)(scope, receive, send)
        else:
            await JSONResponse({"detail": "Sign in to Guardian first"}, status_code=401)(scope, receive, send)


app = FastAPI(title="Guardian", description="Local AI security cameras", version="2.1.0", lifespan=lifespan)

# The dashboard is normally served by this app (same origin). CORS is only for the
# Vite dev server, which proxies requests anyway.
app.add_middleware(CORSMiddleware, allow_origins=list(DEV_ORIGINS), allow_methods=["*"], allow_headers=["*"])
# The last one added runs first: the phone port turns away non-phone paths before any password prompt.
app.add_middleware(DashboardPassword, password=DASHBOARD_PASSWORD)
app.add_middleware(PhonePortGuard, port=PHONE_PORT)
app.include_router(router)
app.include_router(evidence_router)
app.include_router(ws_router)
app.include_router(phone_router)

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="dashboard")
else:
    @app.get("/")
    def no_dashboard():
        return {"message": "Guardian API is running. Build the dashboard with: cd frontend && npm run build",
                "docs": "/docs"}
