"""
Tamper-evident evidence vault.

Every clip and event picture is fingerprinted (SHA-256) when it is saved, and the fingerprint
goes into an append-only ledger, DATA_DIR/vault/ledger.jsonl. Each line holds the file's name,
hash and size, the hash of the line before it, and an Ed25519 signature made with a key that is
created on first start and never leaves this computer. Changing a file changes its hash; editing,
reordering or removing an earlier line breaks the chain. Deleting a file adds a "deleted" line,
so old lines are never edited and the history stays complete.

This proves a file has not changed since Guardian saved it, as long as the private key stayed
private: someone with full access to this computer could sign a new ledger with it.

Hashing and the fsynced ledger writes happen on the vault's own thread; cameras only queue work.
"""
import hashlib
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from config import DATA_DIR, RECORDINGS_DIR, SNAPSHOTS_DIR

VAULT_DIR = DATA_DIR / "vault"
GENESIS = "0" * 64  # "prev" of the first entry
SEALED_KINDS = ("clip", "picture")  # files that live on in the recordings and snapshots folders
MAX_NAMES = 50  # names listed per problem in a full check


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(entry: dict) -> bytes:
    """The signed bytes: the entry without its signature, keys sorted, no spaces."""
    return json.dumps({k: v for k, v in entry.items() if k != "sig"}, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def safe_name(name: str) -> bool:
    return bool(name) and Path(name).name == name and not name.startswith(".") and "\\" not in name


class EvidenceVault:
    def __init__(self, directory: Path = VAULT_DIR, recordings_dir: Path = RECORDINGS_DIR,
                 snapshots_dir: Path = SNAPSHOTS_DIR, events=None):
        self.dir = directory
        self.ledger_path = directory / "ledger.jsonl"
        self.private_path = directory / "private_key.pem"
        self.public_path = directory / "public_key.pem"
        self.check_path = directory / "last_check.json"
        self.folders = {"clip": recordings_dir, "picture": snapshots_dir}
        self.events = events  # logs the results of full checks; set by start() when None
        self._lock = threading.Lock()
        self._loaded = False
        self._key: Ed25519PrivateKey | None = None
        self.public_pem = b""
        self.fingerprint = ""
        self._seq = 0
        self._prev = GENESIS
        self._seals: dict[tuple[str, str], dict] = {}    # (kind, name) -> its seal entry
        self._deleted: dict[tuple[str, str], dict] = {}  # (kind, name) -> its "deleted" entry
        self._verified: dict | None = None  # the ledger prefix already checked, so a check only reads new lines
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[tuple[str, str]] = set()
        self._worker: threading.Thread | None = None
        self.checking = False
        self.last_check: dict | None = None

    # ---- keys and ledger ------------------------------------------------------------------
    def _load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self.dir.mkdir(parents=True, exist_ok=True)
            self._key = self._load_key()
            public = self._key.public_key()
            self.public_pem = public.public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo)
            raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            self.fingerprint = hashlib.sha256(raw).hexdigest()
            if not self.public_path.exists() or self.public_path.read_bytes() != self.public_pem:
                self.public_path.write_bytes(self.public_pem)
            self._read_index()
            try:
                self.last_check = json.loads(self.check_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.last_check = None
            self._loaded = True

    def _load_key(self) -> Ed25519PrivateKey:
        if self.private_path.exists():
            try:
                os.chmod(self.private_path, 0o600)
            except OSError:
                pass
            key = serialization.load_pem_private_key(self.private_path.read_bytes(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise RuntimeError(f"{self.private_path} is not an Ed25519 key")
            return key
        if self.ledger_path.exists() and self.ledger_path.stat().st_size:
            print("[vault] The signing key is missing; entries signed with the old key can no longer be checked")
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        fd = os.open(self.private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
            f.flush()
            os.fsync(f.fileno())
        print(f"[vault] Created a signing key in {self.dir}")
        return key

    def _read_index(self) -> None:
        """Where the chain continues, and the latest entry per file. Signatures are checked by verify()."""
        if not self.ledger_path.exists():
            return
        data = self.ledger_path.read_bytes()
        if data and not data.endswith(b"\n"):
            # A write cut off by a crash or power cut was never confirmed; drop the partial line.
            data = data[:data.rfind(b"\n") + 1]
            with open(self.ledger_path, "r+b") as f:
                f.truncate(len(data))
            print("[vault] Removed an incomplete last line from the ledger")
        for raw in data.split(b"\n")[:-1]:
            self._seq += 1
            self._prev = hashlib.sha256(raw).hexdigest()
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                self._index(entry)

    def _index(self, entry: dict) -> None:
        kind, name = entry.get("kind"), entry.get("name")
        if kind in SEALED_KINDS:
            self._seals[(kind, name)] = entry
            self._deleted.pop((kind, name), None)
        elif kind == "deleted":
            self._deleted[(entry.get("deleted_kind"), name)] = entry

    def _append(self, fields: dict) -> dict:
        self._load()
        with self._lock:
            entry = {"seq": self._seq + 1, "time": utc_iso(), "meta_sha256": None, **fields, "prev": self._prev}
            entry["sig"] = self._key.sign(canonical(entry)).hex()
            line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._seq += 1
            self._prev = hashlib.sha256(line.encode()).hexdigest()
            self._index(entry)
            return entry

    # ---- sealing (on the vault's thread) ---------------------------------------------------------
    def _submit(self, key: tuple[str, str] | None, job: Callable[[], None]) -> None:
        with self._lock:
            if key:
                self._pending.add(key)
            if not self._worker or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, daemon=True, name="vault")
                self._worker.start()
        self._queue.put((key, job))

    def _work(self) -> None:
        while True:
            key, job = self._queue.get()
            try:
                job()
            except Exception as exc:
                print(f"[vault] {exc}")
            finally:
                if key:
                    with self._lock:
                        self._pending.discard(key)
                self._queue.task_done()

    def wait_idle(self, timeout: float = 10) -> bool:
        """For tests and shutdown: waits until queued seals and deletions are written."""
        end = time.time() + timeout
        while self._queue.unfinished_tasks and time.time() < end:
            time.sleep(0.02)
        return not self._queue.unfinished_tasks

    def _seal(self, kind: str, path: Path, late: bool = False) -> dict | None:
        self._load()
        with self._lock:
            sealed = self._seals.get((kind, path.name))
        if sealed or not path.is_file():
            return sealed
        sha, size = file_sha256(path)
        fields = {"kind": kind, "name": path.name, "sha256": sha, "size": size}
        if kind == "clip":
            meta = path.with_suffix(".json")
            fields["meta_sha256"] = file_sha256(meta)[0] if meta.is_file() else None
        if late:
            fields["sealed_late"] = True  # sealed after the fact, e.g. saved before the vault existed
        return self._append(fields)

    def _record_deletion(self, kind: str, name: str, reason: str) -> None:
        self._load()
        with self._lock:
            sealed = self._seals.get((kind, name))
            if not sealed or (kind, name) in self._deleted:
                return  # never sealed (e.g. a failed clip), or already recorded
        self._append({"kind": "deleted", "name": name, "deleted_kind": kind, "sha256": sealed["sha256"],
                      "size": sealed["size"], "meta_sha256": sealed.get("meta_sha256"), "reason": reason})

    def seal_clip(self, path: Path) -> None:
        """Queues a finished clip (and its metadata) for sealing."""
        self._submit(("clip", path.name), lambda: self._seal("clip", path))

    def seal_picture(self, path: Path) -> None:
        self._submit(("picture", path.name), lambda: self._seal("picture", path))

    def clip_deleted(self, path: Path, reason: str) -> None:
        self._submit(None, lambda: self._record_deletion("clip", path.name, reason))

    def picture_deleted(self, name: str, reason: str) -> None:
        self._submit(None, lambda: self._record_deletion("picture", name, reason))

    def seal_report(self, data: bytes, incident: str) -> dict:
        """Seals a generated incident report straight away; it goes into an evidence package."""
        return self._append({"kind": "report", "name": "report.html", "incident": incident,
                             "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})

    def start(self, library=None, events=None) -> None:
        """Loads or creates the key, connects to the recording library and the event log, and seals
        files that were saved while the vault did not exist or Guardian was not running."""
        self._load()
        if events is not None:
            self.events = self.events or events
            events.on_saved = self.seal_picture
            events.on_deleted = self.picture_deleted
        if library is not None:
            library.on_saved = self.seal_clip
            library.on_deleted = self.clip_deleted
        for leftover in DATA_DIR.glob(".package-*.zip"):  # evidence packages from a crash mid-download
            leftover.unlink(missing_ok=True)
        late = {"clip": [], "picture": []}
        with self._lock:
            for kind, pattern in (("clip", "*.mp4"), ("picture", "*.jpg")):
                for path in sorted(self.folders[kind].glob(pattern)):
                    if not path.name.endswith(".part.mp4") and (kind, path.name) not in self._seals:
                        late[kind].append(path)
        for kind, paths in late.items():
            for path in paths:
                self._submit((kind, path.name), lambda k=kind, p=path: self._seal(k, p, late=True))
        if late["clip"] or late["picture"]:
            def report():
                parts = [f"{len(v)} {k}{'s' if len(v) != 1 else ''}" for k, v in late.items() if v]
                self._log(f"Sealed {' and '.join(parts).replace('picture', 'event picture')} that had not been "
                          "sealed when they were saved; they are marked as sealed late", "INFO")
            self._submit(None, report)

    def _log(self, description: str, severity: str) -> None:
        if self.events is not None:
            self.events.log("VAULT", description, severity)

    # ---- checking ----------------------------------------------------------------------------
    def _chain(self) -> tuple[list[dict | None], list[bytes], int | None]:
        """Reads the ledger and checks every link and signature. Returns the entries (None where a line
        can't be read), the raw lines, and the number of the first entry that breaks the chain."""
        self._load()
        with self._lock:
            data = self.ledger_path.read_bytes() if self.ledger_path.exists() else b""
            expected = self._seq
        lines = data.split(b"\n")
        lines = lines[:-1] if data.endswith(b"\n") else lines if data else []
        public = self._key.public_key()
        cached = self._verified
        if cached and len(data) >= cached["length"] and \
                hashlib.sha256(data[:cached["length"]]).hexdigest() == cached["digest"]:
            entries, prev = list(cached["entries"]), cached["prev"]
        else:
            entries, prev = [], GENESIS
        broken = None
        for n in range(len(entries) + 1, len(lines) + 1):
            raw = lines[n - 1]
            try:
                entry = json.loads(raw)
                if not isinstance(entry, dict):
                    raise ValueError("not an entry")
                public.verify(bytes.fromhex(entry["sig"]), canonical(entry))
                ok = entry.get("seq") == n and entry.get("prev") == prev
            except (ValueError, KeyError, TypeError, InvalidSignature):
                entry, ok = None, False
            if not ok and broken is None:
                broken = n
            entries.append(entry)
            prev = hashlib.sha256(raw).hexdigest()
        if broken is None and data.endswith(b"\n"):
            self._verified = {"length": len(data), "digest": hashlib.sha256(data).hexdigest(),
                              "entries": list(entries), "prev": prev}
        if broken is None and len(lines) < expected:
            broken = len(lines) + 1  # lines were removed from the end
        return entries, lines, broken

    def _check_file(self, kind: str, name: str, seal: dict) -> tuple[str, str | None, str | None]:
        """(intact | modified | missing, the file's hash now, what changed)"""
        path = self.folders[kind] / name
        if not path.is_file():
            return "missing", None, None
        sha, size = file_sha256(path)
        if sha != seal.get("sha256") or size != seal.get("size"):
            return "modified", sha, "file"
        if kind == "clip" and seal.get("meta_sha256"):
            meta = path.with_suffix(".json")
            if not meta.is_file() or file_sha256(meta)[0] != seal["meta_sha256"]:
                return "modified", sha, "details"
        return "intact", sha, None

    def verify(self, kind: str, name: str) -> dict:
        """intact | modified | missing | not_sealed | ledger_broken, for one clip or picture."""
        if kind not in SEALED_KINDS or not safe_name(name):
            raise ValueError("Unknown file")
        entries, _, broken = self._chain()
        seal = deleted = None
        seal_n = last_n = 0
        for n, e in enumerate(entries, 1):
            if not e or e.get("name") != name:
                continue
            if e.get("kind") == kind:
                seal, seal_n, deleted, last_n = e, n, None, n
            elif e.get("kind") == "deleted" and e.get("deleted_kind") == kind:
                deleted, last_n = e, n
        result = {"kind": kind, "name": name, "checked_at": utc_iso(), "seq": seal_n or None,
                  "sealed_at": seal and seal.get("time"), "sealed_late": bool(seal and seal.get("sealed_late")),
                  "sha256": seal and seal.get("sha256"), "size": seal and seal.get("size"),
                  "current_sha256": None, "changed": None, "broken_at": None,
                  "deleted": deleted and {"time": deleted.get("time"), "reason": deleted.get("reason")},
                  "exists": (self.folders[kind] / name).is_file()}
        if broken is not None and (seal is None or broken <= last_n):
            return {**result, "status": "ledger_broken", "broken_at": broken}
        if seal is None:
            return {**result, "status": "not_sealed"}
        status, current, changed = self._check_file(kind, name, seal)
        return {**result, "status": status, "current_sha256": current, "changed": changed}

    def known(self, kind: str, name: str) -> bool:
        """Whether the ledger has ever sealed this file."""
        self._load()
        with self._lock:
            return (kind, name) in self._seals

    def seal_info(self, kind: str, name: str) -> dict | None:
        """The seal from the ledger as it was read and written by this process, for listings."""
        self._load()
        with self._lock:
            seal = self._seals.get((kind, name))
        return seal and {"sealed_at": seal.get("time"), "sealed_late": bool(seal.get("sealed_late")),
                         "sha256": seal.get("sha256"), "seq": seal.get("seq")}

    def lines_for(self, files: set[tuple[str, str]]) -> list[str]:
        """The ledger lines that seal or delete these (kind, name) files, in order."""
        entries, lines, _ = self._chain()
        out = []
        for e, raw in zip(entries, lines):
            if e and ((e.get("kind"), e.get("name")) in files or
                      (e.get("kind") == "deleted" and (e.get("deleted_kind"), e.get("name")) in files)):
                out.append(raw.decode())
        return out

    def verify_all(self) -> dict:
        """Checks the whole chain and every sealed file that has not been deleted."""
        started = time.time()
        entries, _, broken = self._chain()
        live: dict[tuple[str, str], tuple[int, dict]] = {}
        deleted = 0
        for n, e in enumerate(entries, 1):
            if not e:
                continue
            if e.get("kind") in SEALED_KINDS:
                live[(e["kind"], e.get("name"))] = (n, e)
            elif e.get("kind") == "deleted":
                deleted += 1
                live.pop((e.get("deleted_kind"), e.get("name")), None)
        found = {"intact": 0, "modified": [], "missing": [], "unverifiable": 0}
        for (kind, name), (n, seal) in sorted(live.items(), key=lambda item: item[1][0]):
            if broken is not None and broken <= n:
                found["unverifiable"] += 1
                continue
            status, _, _ = self._check_file(kind, name, seal)
            if status == "intact":
                found["intact"] += 1
            else:
                found[status].append(name)
        with self._lock:
            # Files being sealed right now, and files whose seal is in a damaged part of the ledger
            skip = self._pending | set(self._seals)
        not_sealed = [p.name for kind, pattern in (("clip", "*.mp4"), ("picture", "*.jpg"))
                      for p in sorted(self.folders[kind].glob(pattern))
                      if not p.name.endswith(".part.mp4") and (kind, p.name) not in live and (kind, p.name) not in skip]
        return {
            "checked_at": utc_iso(),
            "seconds": round(time.time() - started, 1),
            "entries": len(entries),
            "broken_at": broken,
            "files": len(live),
            "intact": found["intact"],
            "modified": found["modified"][:MAX_NAMES],
            "modified_count": len(found["modified"]),
            "missing": found["missing"][:MAX_NAMES],
            "missing_count": len(found["missing"]),
            "unverifiable": found["unverifiable"],
            "not_sealed": not_sealed[:MAX_NAMES],
            "not_sealed_count": len(not_sealed),
            "deleted": deleted,
        }

    def start_full_check(self) -> bool:
        """Runs verify_all in the background. False if one is already running."""
        with self._lock:
            if self.checking:
                return False
            self.checking = True

        def run():
            try:
                summary = self.verify_all()
                self.last_check = summary
                try:
                    self.check_path.write_text(json.dumps(summary), encoding="utf-8")
                except OSError as exc:
                    print(f"[vault] Could not save the check result: {exc}")
                self._log(describe_check(summary), "INFO" if check_ok(summary) else "HIGH")
            except Exception as exc:
                print(f"[vault] Full check failed: {exc}")
            finally:
                self.checking = False

        threading.Thread(target=run, daemon=True, name="vault-check").start()
        return True

    def status(self) -> dict:
        self._load()
        with self._lock:
            live = [k for k in self._seals if k not in self._deleted]
            return {
                "fingerprint": self.fingerprint,
                "created": datetime.fromtimestamp(self.private_path.stat().st_mtime, timezone.utc)
                .isoformat(timespec="seconds").replace("+00:00", "Z") if self.private_path.exists() else None,
                "entries": self._seq,
                "clips": sum(1 for kind, _ in live if kind == "clip"),
                "pictures": sum(1 for kind, _ in live if kind == "picture"),
                "pending": len(self._pending),
                "checking": self.checking,
                "last_check": self.last_check,
            }


def check_ok(summary: dict) -> bool:
    return not (summary["broken_at"] or summary["modified_count"] or summary["missing_count"])


def describe_check(summary: dict) -> str:
    files = f"{summary['files']} sealed file{'s' if summary['files'] != 1 else ''}"
    if check_ok(summary):
        return f"Verified {files}: all intact, ledger unbroken"
    problems = []
    if summary["broken_at"]:
        problems.append(f"ledger broken at entry {summary['broken_at']}")
    if summary["modified_count"]:
        problems.append(f"{summary['modified_count']} modified after sealing")
    if summary["missing_count"]:
        problems.append(f"{summary['missing_count']} missing without a deletion record")
    return f"Verified {files}: {', '.join(problems)}"


evidence_vault = EvidenceVault()
