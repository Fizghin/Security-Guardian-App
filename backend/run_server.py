"""
Start Guardian:  python backend/run_server.py   (from any directory)

HOST / PORT come from backend/.env (defaults 127.0.0.1:8000). Set HOST=0.0.0.0
to open the dashboard from other devices on your network.

If the language model is a local Ollama server that is not running yet, it is
started here so warnings come from the model rather than pre-written lines.
"""
import os
import platform
import shutil
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

from config import FRONTEND_DIST, HOST, PORT  # noqa: E402
from services.ai_service import AIError, ai_service  # noqa: E402
from services.settings_service import settings_service  # noqa: E402


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
    url = f"http://{'localhost' if HOST in ('0.0.0.0', '127.0.0.1') else HOST}:{PORT}"
    if not (FRONTEND_DIST / "index.html").exists():
        print("Dashboard not built yet: run `npm install && npm run build` in frontend/ (install scripts do this).")
    ensure_local_model()
    print(f"\nGuardian dashboard: {url}   (Ctrl+C to stop)\n")
    if "--no-browser" not in sys.argv:
        Timer(3.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False, log_level="warning")
