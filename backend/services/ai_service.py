"""
Voice warnings written by a local language model.

Supported back ends (both run on the local machine):
  * Ollama            - provider "ollama", base URL http://localhost:11434
  * OpenAI-compatible - provider "openai", e.g. LM Studio (http://localhost:1234/v1)
                        or llama.cpp server (http://localhost:8080/v1)

Reliability measures, because small local models drift:
  * every request carries an explicit list of facts (recording, owner alerted,
    siren, people, location, time of day) and nothing else may be claimed;
  * replies are checked: invented police/guards/dogs/weapons, names, or claims
    that contradict the facts are rejected, retried once, then replaced by a
    pre-written line that is true right now;
  * one line per level is generated ahead of time while nothing is happening,
    so the first warning of an incident plays immediately even on a slow CPU.
"""
import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import httpx

from config import DATA_DIR
from data.fallback_messages import get_fallback_message
from services.settings_service import AISettings, settings_service


class AIError(RuntimeError):
    pass


# On CPU-only machines the model and the person detector compete for cores. Giving the
# model half of them keeps the camera feeds and detection responsive while it is talking.
LLM_THREADS = max(2, (os.cpu_count() or 4) // 2)
CACHE_FILE = DATA_DIR / "voice_cache.json"
CACHE_PER_KEY = 2
# Reading a model from a slow disk can take minutes. If the request gives up early, Ollama
# abandons the half-finished load, so loading gets its own generous timeout.
LOAD_TIMEOUT = 600
KEEP_ALIVE = "30m"
REWARM_SECONDS = 600  # while armed, refresh well within KEEP_ALIVE so the model stays in memory

SITUATIONS = {
    1: "An unrecognised person has just appeared on camera. Greet them and ask who they are or what they need.",
    2: "The person is still here. Tell them clearly this is private property and they should leave.",
    3: "The person is ignoring the warnings. Tell them firmly to leave now.",
    4: "Final warning. Order the person to leave immediately.",
}
PANIC_SITUATION = "The owner raised the alarm manually. Tell the person on camera to leave immediately."

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LABEL_RE = re.compile(r"^\s*(guardian|ai|security|system|assistant|voice|speaker)\s*:\s*", re.IGNORECASE)
_BANNED = re.compile(
    r"\b(police|cops?|officers?|sheriff|guards?|(our|my|the|security|by) (staff|team|personnel|people|men|crew)|"
    r"removed|thrown out|escort\w*|authorit(y|ies)|neighbou?rs?|backup|patrol|dogs?|k-?9|weapons?|guns?|"
    r"armed (response|guards?)|shoot|kill|hurt|harm|arrest\w*|jail|prison|lawyers?|sue|drones?|thermal|"
    r"dispatch\w*|on (their|the|its) way|my name is|sergeant|agent|detective|fine of|fined)\b", re.IGNORECASE)
# Case-insensitive lead-in, but the name itself must be capitalised ("This is Sarah", not "this is private").
_SELF_NAME = re.compile(r"\b(?i:i am|i'm|this is|my name is)\s+(?!(?i:the|a|an|your|private|not)\b)[A-Z][a-z]+")
_INTRO = re.compile(r"^(i am|i'm)\b|\b(this is|i am|i'm) (the|your) (security|alarm|guardian|surveillance|camera|home)",
                    re.IGNORECASE)
_REFUSAL = re.compile(r"\b(i can(no|')t (help|provide|assist|do)|i'?m (not able|unable)|as an ai|language model|"
                      r"something else i can help|i won'?t be able)\b", re.IGNORECASE)
_OWNER_TOLD = re.compile(r"\b(owners?|homeowners?|residents?|family)\b[^.!?]*\b(alert|notif|inform|told|contact|call)",
                         re.IGNORECASE)


TIMING = ("They have just arrived", "They have stayed for a while",
          "They have ignored earlier warnings", "They have ignored several warnings")


@dataclass
class WarningContext:
    level: int
    people: int = 1
    location: str = ""
    recording: bool = False
    alerted: bool = False
    siren: bool = False
    siren_next: bool = False
    manual: bool = False
    said: list[str] = field(default_factory=list)

    def facts(self) -> set[str]:
        return {name for name, on in (("rec", self.recording), ("alert", self.alerted), ("siren", self.siren),
                                      ("siren_next", self.siren_next)) if on}

    def cache_key(self) -> str:
        return f"{4 if self.manual else self.level}:{''.join(sorted(self.facts()))}"


def time_of_day(hour: int | None = None) -> str:
    hour = datetime.now().hour if hour is None else hour
    return "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 18 else "evening" if hour < 22 else "night"


def _tone(cfg: AISettings) -> str:
    if cfg.intimidation >= 67:
        tone = "stern and intimidating"
    elif cfg.intimidation >= 34:
        tone = "firm and authoritative"
    else:
        tone = "calm and polite"
    if cfg.humor >= 67:
        tone += ", with sarcastic wit"
    elif cfg.humor >= 34:
        tone += ", with a touch of dry humour"
    return tone


def build_system_prompt(cfg: AISettings) -> str:
    return (
        "You are the voice of a property's security system, speaking through a loudspeaker "
        "to a person seen on camera. Speak directly to that person.\n"
        f"Tone: {_tone(cfg)}.\n"
        "Rules: reply with one or two short sentences, under 30 words in total. "
        "Plain spoken English only: no quotation marks, emojis, stage directions, lists or labels. "
        "Do not introduce yourself and never give yourself a name or job title. "
        "Use only the facts you are given. Never mention police, guards, dogs, weapons, damage or punishment."
    )


def build_user_message(ctx: WarningContext, generic: bool = False) -> str:
    situation = PANIC_SITUATION if ctx.manual else SITUATIONS.get(ctx.level, SITUATIONS[1])
    facts = []
    if ctx.location and not generic:
        facts.append(f"Camera location: {ctx.location}")
    if not generic:
        facts.append(f"People on camera: {ctx.people}")
    if not ctx.manual:
        # Qualitative on purpose: small models misquote exact numbers.
        facts.append(TIMING[ctx.level - 1])
    facts += [
        f"Time of day: {time_of_day()}" if not generic else None,
        f"Video is being recorded: {'yes' if ctx.recording else 'no'}",
        f"The owner has been alerted: {'yes' if ctx.alerted else 'no'}",
        f"An alarm siren is sounding: {'yes' if ctx.siren else 'no'}",
        "The siren will sound if they stay: yes" if ctx.siren_next and not ctx.siren else None,
    ]
    lines = [situation, "Facts (mention only these):"] + [f"- {f}" for f in facts if f]
    if ctx.said:
        lines.append("Already said (say something different):")
        lines += [f"- {line}" for line in ctx.said[-3:]]
    lines.append("Reply with exactly what to say.")
    return "\n".join(lines)


def clean_response(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    text = text.replace("*", "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = " ".join(lines)
    text = _LABEL_RE.sub("", text)
    text = text.strip().strip('"').strip("'").strip("“”").strip()
    text = re.sub(r"\s+", " ", text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(sentences[:2])
    return text[:280].strip()


def check_reply(text: str, ctx: WarningContext) -> str | None:
    """Returns why a reply must not be spoken, or None if it is fine."""
    words = len(text.split())
    if words < 3:
        return "too short"
    if words > 40:
        return "too long"
    if not re.search(r"[A-Za-z]", text) or re.search(r"[|#<>{}\[\]]", text):
        return "contains formatting instead of plain speech"
    if _REFUSAL.search(text):
        return "is a refusal, not a warning"
    m = _BANNED.search(text)
    if m:
        return f"mentions '{m.group(0)}', which is not a fact"
    if _SELF_NAME.search(text):
        return "gives itself a name"
    if _INTRO.search(text):
        return "introduces itself instead of speaking to the person"
    if re.search(r"\bnot (being )?record", text, re.IGNORECASE):
        return "says it is not recording"
    if not ctx.recording and re.search(r"\brecord", text, re.IGNORECASE):
        return "claims recording, but the camera is not recording"
    if not ctx.alerted and _OWNER_TOLD.search(text):
        return "claims the owner was alerted, but no alert was sent"
    if not (ctx.siren or ctx.siren_next) and re.search(r"\b(siren|alarm)\b", text, re.IGNORECASE):
        return "mentions an alarm that is not part of this response"
    if any(_similar(text, s) for s in ctx.said):
        return "repeats an earlier line"
    if any(_similar(text, t.replace("They have", "You have")) for t in TIMING):
        return "only restates the facts instead of speaking to the person"
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.7


class AIService:
    def __init__(self, settings=settings_service, cache_file=CACHE_FILE):
        self.settings = settings
        self.cache_file = cache_file
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai")
        self._lock = threading.Lock()
        self._pending: set[str] = set()     # cameras with a warning being written
        self._prefetching = False
        self._model_cache: tuple[tuple, str, float] | None = None
        self._lines: dict[str, deque] = {}
        self._signature = self._settings_signature()
        self.last_error: str | None = None
        self.last_latency_ms: int | None = None
        self.last_source: str | None = None  # llm | cached | fallback
        self.last_model: str | None = None
        self.rejected = 0
        self.loading = False
        self._warm_attempt = 0.0
        self._load_cache()
        settings.on_change(self._on_settings_change)

    # ---- settings / cache -------------------------------------------------
    def _settings_signature(self) -> str:
        cfg = self.settings.get().ai
        raw = f"{cfg.provider}|{cfg.base_url}|{cfg.model}|{cfg.intimidation}|{cfg.humor}|{build_system_prompt(cfg)}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def _on_settings_change(self, old, new) -> None:
        if old.ai != new.ai:
            self._model_cache = None
            self.last_error = None
        sig = self._settings_signature()
        if sig != self._signature:
            with self._lock:
                self._signature = sig
                self._lines.clear()
            self._save_cache()

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if data.get("signature") == self._signature:
                self._lines = {k: deque(v, maxlen=CACHE_PER_KEY) for k, v in data.get("lines", {}).items()}
        except (OSError, ValueError, AttributeError):
            pass

    def _save_cache(self) -> None:
        try:
            with self._lock:
                data = {"signature": self._signature, "lines": {k: list(v) for k, v in self._lines.items()}}
            self.cache_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def take_cached(self, ctx: WarningContext) -> str | None:
        with self._lock:
            lines = self._lines.get(ctx.cache_key())
            while lines:
                text = lines.popleft()
                if check_reply(text, ctx) is None:
                    break
            else:
                return None
        self._save_cache()
        return text

    def cached_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._lines.values())

    # ---- server access -------------------------------------------------
    @staticmethod
    def _base(cfg: AISettings) -> str:
        return cfg.base_url.strip().rstrip("/")

    @staticmethod
    def _headers(cfg: AISettings) -> dict:
        return {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}

    def list_models(self, cfg: AISettings | None = None) -> list[str]:
        cfg = cfg or self.settings.get().ai
        try:
            if cfg.provider == "ollama":
                r = httpx.get(f"{self._base(cfg)}/api/tags", timeout=5)
                r.raise_for_status()
                return sorted(m["name"] for m in r.json().get("models", []))
            r = httpx.get(f"{self._base(cfg)}/models", headers=self._headers(cfg), timeout=5)
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []))
        except httpx.ConnectError as exc:
            raise AIError(f"Cannot reach {cfg.provider} at {cfg.base_url}. Is it running?") from exc
        except httpx.HTTPStatusError as exc:
            raise AIError(f"{cfg.provider} returned HTTP {exc.response.status_code} for {exc.request.url}") from exc
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise AIError(f"Model list request failed: {exc}") from exc

    def resolve_model(self, cfg: AISettings) -> str:
        key = (cfg.provider, self._base(cfg), cfg.model)
        if self._model_cache and self._model_cache[0] == key and time.time() - self._model_cache[2] < 60:
            return self._model_cache[1]
        models = self.list_models(cfg)
        if not models:
            hint = "ollama pull llama3.2:3b" if cfg.provider == "ollama" else "load a model in your server"
            raise AIError(f"No models installed. Run: {hint}")
        if cfg.model:
            # Ollama accepts "llama3.2" for "llama3.2:latest"
            matches = [m for m in models if m == cfg.model or m == f"{cfg.model}:latest"]
            if not matches:
                raise AIError(f"Model '{cfg.model}' is not installed. Available: {', '.join(models[:8])}")
            model = matches[0]
        else:
            model = models[0]
        self._model_cache = (key, model, time.time())
        return model

    def _chat(self, cfg: AISettings, model: str, messages: list[dict], temperature: float = 0.7,
              timeout: httpx.Timeout | None = None) -> str:
        timeout = timeout or httpx.Timeout(cfg.timeout_seconds, connect=5)
        if cfg.provider == "ollama":
            r = httpx.post(f"{self._base(cfg)}/api/chat", timeout=timeout, json={
                "model": model,
                "messages": messages,
                "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_predict": 80, "temperature": temperature, "num_thread": LLM_THREADS},
            })
            r.raise_for_status()
            return r.json()["message"]["content"]
        r = httpx.post(f"{self._base(cfg)}/chat/completions", headers=self._headers(cfg), timeout=timeout, json={
            "model": model,
            "messages": messages,
            "max_tokens": 80,
            "temperature": temperature,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _write_line(self, cfg: AISettings, model: str, ctx: WarningContext, generic: bool = False) -> str:
        """Ask the model for a line; one retry with feedback if the first answer breaks the rules."""
        messages = [{"role": "system", "content": build_system_prompt(cfg)},
                    {"role": "user", "content": build_user_message(ctx, generic)}]
        text = clean_response(self._chat(cfg, model, messages))
        problem = check_reply(text, ctx)
        if problem is None:
            return text
        self.rejected += 1
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That was not acceptable: it {problem}. Write a new line that "
                                                 "uses only the listed facts."}]
        text = clean_response(self._chat(cfg, model, messages, temperature=0.4))
        problem = check_reply(text, ctx)
        if problem is None:
            return text
        self.rejected += 1
        raise AIError(f"Model reply rejected ({problem})")

    def ask(self, messages: list[dict], temperature: float = 0.7) -> tuple[str, str]:
        """One request to the configured model for other spoken lines (the Guard Bot's replies).
        Returns (cleaned reply, model); raises AIError or httpx errors like the warnings do."""
        cfg = self.settings.get().ai
        model = self.resolve_model(cfg)
        return clean_response(self._chat(cfg, model, messages, temperature)), model

    def submit(self, job: Callable[[], None]) -> None:
        """Runs a job on the model's worker, in turn with the warnings."""
        self._executor.submit(job)

    # ---- generation ----------------------------------------------------
    def generate_warning(self, ctx: WarningContext) -> dict:
        cfg = self.settings.get().ai
        started = time.time()
        try:
            model = self.resolve_model(cfg)
            text = self._write_line(cfg, model, ctx)
            self.last_error, self.last_source, self.last_model = None, "llm", model
        except (AIError, httpx.HTTPError, KeyError, ValueError) as exc:
            msg = str(exc) or exc.__class__.__name__
            if isinstance(exc, httpx.TimeoutException):
                msg = ("Model is still loading" if self.loading
                       else f"Model did not answer within {cfg.timeout_seconds}s")
            self.last_error, self.last_source, model = msg, "fallback", None
            print(f"[ai] Using pre-written line: {msg}")
            text = get_fallback_message(4 if ctx.manual else ctx.level, cfg.intimidation, cfg.humor,
                                        ctx.facts(), ctx.said)
        self.last_latency_ms = int((time.time() - started) * 1000)
        return {"text": text, "source": self.last_source, "model": model, "latency_ms": self.last_latency_ms,
                "error": self.last_error}

    def request_warning(self, callback: Callable[[dict], None], ctx: WarningContext, camera_id: str = "") -> bool:
        """Generate in the background. Returns False if this camera already has one pending."""
        with self._lock:
            if camera_id in self._pending:
                return False
            self._pending.add(camera_id)

        def run():
            try:
                callback(self.generate_warning(ctx))
            except Exception as exc:
                print(f"[ai] Warning task failed: {exc}")
            finally:
                with self._lock:
                    self._pending.discard(camera_id)

        self._executor.submit(run)
        return True

    def prefetch(self, contexts: list[WarningContext]) -> bool:
        """While nothing is happening, write one missing cached line. Returns True if work was queued."""
        with self._lock:
            if self._pending or self._prefetching or self.loading:
                return False
            todo = next((c for c in contexts if len(self._lines.get(c.cache_key(), ())) < CACHE_PER_KEY), None)
            if todo is None:
                return False
            self._prefetching = True
            signature = self._signature

        def run():
            try:
                cfg = self.settings.get().ai
                model = self.resolve_model(cfg)
                with self._lock:
                    existing = list(self._lines.get(todo.cache_key(), ()))
                ctx = WarningContext(**{**todo.__dict__, "said": existing})
                text = self._write_line(cfg, model, ctx, generic=True)
                with self._lock:
                    if signature == self._signature:
                        self._lines.setdefault(todo.cache_key(), deque(maxlen=CACHE_PER_KEY)).append(text)
                self._save_cache()
            except Exception as exc:
                self.last_error = str(exc) or exc.__class__.__name__
            finally:
                with self._lock:
                    self._prefetching = False

        self._executor.submit(run)
        return True

    def status(self) -> dict:
        cfg = self.settings.get().ai
        return {
            "provider": cfg.provider,
            "base_url": cfg.base_url,
            "model": self.last_model or cfg.model or None,
            "busy": bool(self._pending),
            "loading": self.loading,
            "last_source": self.last_source,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
            "ready_lines": self.cached_count(),
            "rejected_replies": self.rejected,
        }

    def _load(self, cfg: AISettings, model: str) -> None:
        timeout = httpx.Timeout(LOAD_TIMEOUT, connect=5)
        if cfg.provider == "ollama":
            # An empty request loads the model without generating anything
            r = httpx.post(f"{self._base(cfg)}/api/generate", timeout=timeout,
                           json={"model": model, "keep_alive": KEEP_ALIVE})
            r.raise_for_status()
        else:
            self._chat(cfg, model, [{"role": "user", "content": "Reply with: ready"}], timeout=timeout)

    def warm_up(self, quiet: bool = False) -> bool:
        """Load the model into memory in the background so warnings don't wait for it.
        Returns False if a load is already running."""
        with self._lock:
            if self.loading:
                return False
            self.loading = True
            self._warm_attempt = time.time()

        def run():
            cfg = self.settings.get().ai
            started = time.time()
            try:
                model = self.resolve_model(cfg)
                if not quiet:
                    print(f"[ai] Loading {cfg.provider} model '{model}'…")
                self._load(cfg, model)
                self.last_model, self.last_error = model, None
                if not quiet:
                    print(f"[ai] Model '{model}' ready ({time.time() - started:.0f}s)")
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                if isinstance(exc, httpx.TimeoutException):
                    error = f"Model did not load within {LOAD_TIMEOUT // 60} minutes"
                if not quiet or error != self.last_error:
                    print(f"[ai] Model not ready: {error}")
                self.last_error = error
            finally:
                with self._lock:
                    self.loading = False
            if self.settings.get().ai != cfg:  # settings changed during the load
                self.warm_up()

        threading.Thread(target=run, daemon=True, name="ai-warmup").start()
        return True

    def keep_warm(self) -> None:
        """Called periodically while armed so the model is never unloaded or cold when needed."""
        if time.time() - self._warm_attempt >= REWARM_SECONDS:
            self.warm_up(quiet=True)


ai_service = AIService()
