"""
Speak a line through the local speakers.

Runs as a separate process so a misbehaving speech engine can never hang or
crash the server (pyttsx3's SAPI driver on Windows does not like threads).

Engine per platform (most reliable first):
  Windows  pyttsx3 (SAPI5, built in)
  macOS    `say` (built in), then pyttsx3
  Linux    espeak-ng / espeak / spd-say command line, then pyttsx3

    python speak.py [--rate 165] "text to say"
    python speak.py --check        # prints JSON describing the available engine
"""
import argparse
import json
import platform
import shutil
import subprocess
import sys

SYSTEM = platform.system()


def _cli_engine():
    if SYSTEM == "Darwin" and shutil.which("say"):
        return "say"
    for exe in ("espeak-ng", "espeak", "spd-say"):
        if shutil.which(exe):
            return exe
    return None


def _speak_cli(engine, text, rate):
    if engine == "say":
        cmd = ["say", "-r", str(rate), text]
    elif engine == "spd-say":
        cmd = ["spd-say", "--wait", "-r", str(max(-100, min(100, rate - 175))), text]
    else:
        cmd = [engine, "-s", str(rate), text]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    # espeak exits 0 even when it cannot open the sound device; it only prints errors.
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 or any(line.startswith("error") for line in err.splitlines()):
        raise RuntimeError(f"{engine} could not play audio (no output device?): {err.splitlines()[-1] if err else proc.returncode}")


def _pyttsx3_usable():
    """pyttsx3 on Linux plays through `aplay` and silently does nothing without it."""
    if SYSTEM == "Linux" and not shutil.which("aplay"):
        return False, "pyttsx3 needs aplay (alsa-utils) on Linux"
    try:
        import pyttsx3
        engine = pyttsx3.init()
        return True, f"pyttsx3 ({len(engine.getProperty('voices') or [])} voices)"
    except Exception as exc:
        return False, f"pyttsx3: {exc}"


def _speak_pyttsx3(text, rate):
    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    engine.setProperty("volume", 1.0)
    engine.say(text)
    engine.runAndWait()


def _order():
    cli = _cli_engine()
    if SYSTEM == "Windows":
        return ["pyttsx3"] + ([cli] if cli else [])
    return ([cli] if cli else []) + ["pyttsx3"]


def check():
    errors = []
    for engine in _order():
        if engine == "pyttsx3":
            ok, info = _pyttsx3_usable()
            if ok:
                return {"ok": True, "engine": info, "error": None}
            errors.append(info)
        else:
            return {"ok": True, "engine": engine, "error": None}
    hint = "install espeak-ng" if SYSTEM == "Linux" else "no speech engine found"
    return {"ok": False, "engine": None, "error": "; ".join(errors) or hint}


def speak(text, rate):
    errors = []
    for engine in _order():
        try:
            if engine == "pyttsx3":
                ok, info = _pyttsx3_usable()
                if not ok:
                    raise RuntimeError(info)
                _speak_pyttsx3(text, rate)
            else:
                _speak_cli(engine, text, rate)
            return
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("; ".join(errors) or "No speech engine available")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=int, default=165)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("text", nargs="?")
    args = parser.parse_args()

    if args.check:
        print(json.dumps(check()))
        return 0
    if not args.text:
        parser.error("text is required")
    try:
        speak(args.text, args.rate)
    except Exception as exc:
        print(f"TTS error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
