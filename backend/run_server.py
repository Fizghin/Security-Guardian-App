"""
Start Guardian:  python backend/run_server.py   (from any directory)

HOST / PORT come from backend/.env (defaults 127.0.0.1:8000). Set HOST=0.0.0.0
to open the dashboard from other devices on your network. If another program
already uses PORT, the next free port is used instead.

If the language model is a local Ollama server that is not running yet, it is
started here so warnings come from the model rather than pre-written lines.
"""
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from threading import Timer
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from config import FRONTEND_DIST, HOST, PHONE_PORT, PORT  # noqa: E402
from services.ai_service import AIError, ai_service  # noqa: E402
from services.settings_service import settings_service  # noqa: E402


def port_in_use(host: str, port: int) -> bool:
    """True if something already accepts connections on this port."""
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((target, port)) == 0


def guardian_running(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    try:
        return httpx.get(f"http://{target}:{port}/api/health", timeout=2, trust_env=False).json().get("app") == "guardian"
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def choose_port(host: str, port: int) -> tuple[int, bool]:
    """Returns (port, already_running): the configured port, or the next free one if another
    program has it. already_running means Guardian itself was found there."""
    for candidate in range(port, port + 50):
        if not port_in_use(host, candidate):
            if candidate != port:
                print(f"Port {port} is used by another program, so Guardian uses {candidate}. "
                      "Set PORT= in backend/.env to choose a different one.")
            return candidate, False
        if guardian_running(host, candidate):
            return candidate, True
    sys.exit(f"Ports {port}-{port + 49} are all in use. Set PORT= in backend/.env to a free port.")


def ensure_camera_access() -> None:
    """macOS only lets OpenCV ask for camera access from the main thread, so ask here once."""
    if platform.system() != "Darwin":
        return
    from services.camera_access import AUTHORIZED, local_camera_blocked, request_camera_access
    from services.sources import parse_source
    if not any(c.enabled and parse_source(c.source)[0] in ("auto", "index") for c in settings_service.get().cameras):
        return
    if request_camera_access() not in (None, AUTHORIZED):
        print(f"Camera: {local_camera_blocked()} Phone cameras work either way.")


def find_ollama() -> str | None:
    exe = shutil.which("ollama")
    if exe:
        return exe
    if platform.system() == "Windows":
        candidate = Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.exists():
            return str(candidate)
    if platform.system() == "Darwin" and Path("/Applications/Ollama.app").exists():
        return "/Applications/Ollama.app/Contents/Resources/ollama"
    return None


def ensure_local_model() -> None:
    cfg = settings_service.get().ai
    base = cfg.base_url.rstrip("/")
    if cfg.provider != "ollama":
        print(f"Language model: OpenAI-compatible server at {base}")
        return
    if urlparse(base).hostname not in ("localhost", "127.0.0.1", "::1"):
        print(f"Language model: Ollama at {base}")
        return

    def models():
        r = httpx.get(f"{base}/api/tags", timeout=2)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    try:
        installed = models()
    except httpx.HTTPError:
        exe = find_ollama()
        if not exe:
            print("Language model: Ollama is not installed. Get it from https://ollama.com, then run\n"
                  "  ollama pull llama3.2:3b\n"
                  "Until then Guardian speaks pre-written warnings.")
            return
        print("Language model: starting Ollama…")
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([exe, "serve"], **kwargs)
        installed = None
        for _ in range(20):
            time.sleep(0.5)
            try:
                installed = models()
                break
            except httpx.HTTPError:
                continue
        if installed is None:
            print("Language model: Ollama did not start. Run `ollama serve` yourself to see why.")
            return

    if not installed:
        print("Language model: Ollama is running but has no models. Run:\n  ollama pull llama3.2:3b")
        return
    try:
        print(f"Language model: Ollama · {ai_service.resolve_model(cfg)}  (installed: {', '.join(sorted(installed))})")
    except AIError as exc:
        print(f"Language model: {exc}")


if __name__ == "__main__":
    port, already_running = choose_port(HOST, PORT)
    url = f"http://{'localhost' if HOST in ('0.0.0.0', '127.0.0.1') else HOST}:{port}"
    if already_running:
        print(f"Guardian is already running: {url}")
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        sys.exit(0)
    if not (FRONTEND_DIST / "index.html").exists():
        print("Dashboard not built yet: run `npm install && npm run build` in frontend/ (install scripts do this).")
    ensure_local_model()
    ensure_camera_access()
    print(f"\nGuardian dashboard: {url}   (Ctrl+C to stop)")
    if PHONE_PORT > 0:
        from services.phone_service import lan_addresses
        ips = lan_addresses()
        where = f"https://{ips[0]}:{PHONE_PORT}" if ips else f"port {PHONE_PORT}"
        print(f"Phone cameras connect to {where} (pair them in Settings → Cameras)")
    print()
    if "--no-browser" not in sys.argv:
        Timer(3.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("main:app", host=HOST, port=port, reload=False, log_level="warning")
