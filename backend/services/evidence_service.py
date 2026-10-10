"""
Tamper-evident evidence vault for recordings.

When a clip is saved, its SHA-256 (and that of its metadata file) is written to an
append-only ledger. Every ledger entry includes the hash of the entry before it, so
entries can't be edited, removed or reordered without breaking the chain, and each
entry is signed with an Ed25519 key that is created on this computer and never leaves it.
The public key can be handed to anyone who needs to check the signatures.

Deleting a clip (by hand or through the retention period) appends a "removed" entry,
so a clip that is missing without one stands out.

Verifying a clip recomputes its hashes and compares them with the sealed ones: any
change to the video, even one byte, shows as "tampered".
"""
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR, RECORDINGS_DIR

GENESIS = "0" * 64
CHUNK = 1024 * 1024


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k not in ("hash", "sig")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EvidenceVault:
    def __init__(self, directory: Path = DATA_DIR / "evidence", recordings: Path = RECORDINGS_DIR):
        self.dir = directory
        self.recordings = recordings
        self.ledger = directory / "ledger.jsonl"
        self.key_file = directory / "signing.key"
        self._lock = threading.RLock()
        self._key = None
        self._entries: list[dict] | None = None  # cached ledger, loaded on first use

    # ---- keys --------------------------------------------------------------------
    def _private_key(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        if self._key is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            if self.key_file.exists():
                self._key = serialization.load_pem_private_key(self.key_file.read_bytes(), password=None)
            else:
                self._key = Ed25519PrivateKey.generate()
                pem = self._key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption())
                self.key_file.write_bytes(pem)
                try:
                    self.key_file.chmod(0o600)
                except OSError:
                    pass
        return self._key

    def public_key_pem(self) -> str:
        from cryptography.hazmat.primitives import serialization
        return self._private_key().public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def fingerprint(self) -> str:
        """Short fingerprint of the public key, to compare with a printed report."""
        from cryptography.hazmat.primitives import serialization
        raw = self._private_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        digest = hashlib.sha256(raw).hexdigest()[:24]
        return ":".join(digest[i:i + 4] for i in range(0, len(digest), 4))

    def _sign(self, digest: str) -> str:
        return self._private_key().sign(bytes.fromhex(digest)).hex()

    def _signature_ok(self, digest: str, sig: str) -> bool:
        from cryptography.exceptions import InvalidSignature
        try:
            self._private_key().public_key().verify(bytes.fromhex(sig), bytes.fromhex(digest))
            return True
        except (InvalidSignature, ValueError):
            return False

    # ---- ledger ------------------------------------------------------------------
    def entries(self) -> list[dict]:
        with self._lock:
            if self._entries is None:
                self._entries = []
                if self.ledger.exists():
                    for line in self.ledger.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            try:
                                self._entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                self._entries.append({"corrupt": line[:200]})
            return list(self._entries)

    def _append(self, body: dict) -> dict:
        with self._lock:
            entries = self.entries()
            prev = entries[-1].get("hash", GENESIS) if entries else GENESIS
            entry = {"seq": len(entries) + 1, "time": _now_iso(), **body, "prev": prev}
            entry["hash"] = _entry_hash(entry)
            entry["sig"] = self._sign(entry["hash"])
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
            self._entries.append(entry)
            return entry

    def _latest(self, file: str) -> dict | None:
        for entry in reversed(self.entries()):
            if entry.get("file") == file:
                return entry
        return None

    def sealed_files(self) -> set[str]:
        """Clips whose latest ledger entry seals them (not removed since)."""
        latest: dict[str, str] = {}
        for entry in self.entries():
            if entry.get("file"):
                latest[entry["file"]] = entry.get("action", "")
        return {f for f, action in latest.items() if action == "sealed"}

    # ---- actions -----------------------------------------------------------------
    def seal(self, video: Path, backfilled: bool = False) -> dict | None:
        """Record a saved clip's hashes. Returns the ledger entry."""
        video = Path(video)
        try:
            meta = video.with_suffix(".json")
            body = {
                "action": "sealed",
                "file": video.name,
                "size": video.stat().st_size,
                "sha256": file_sha256(video),
                "meta_sha256": file_sha256(meta) if meta.exists() else None,
            }
            if backfilled:
                body["backfilled"] = True  # sealed after the fact, e.g. clips from before the vault existed
            entry = self._append(body)
            print(f"[vault] Sealed {video.name} ({body['sha256'][:12]}…)")
            return entry
        except Exception as exc:  # never break recording over the vault
            print(f"[vault] Could not seal {video.name}: {exc}")
            return None

    def record_removal(self, file: str, reason: str) -> None:
        try:
            if file in self.sealed_files():
                self._append({"action": "removed", "file": file, "reason": reason})
        except Exception as exc:
            print(f"[vault] Could not record removal of {file}: {exc}")

    def seal_unsealed(self) -> int:
        """Seal clips saved before the vault existed (or while it failed)."""
        sealed = self.sealed_files()
        count = 0
        for video in sorted(self.recordings.glob("*.mp4")):
            if video.name.endswith(".part.mp4") or video.name in sealed:
                continue
            if self.seal(video, backfilled=True):
                count += 1
        return count

    # ---- verification ------------------------------------------------------------
    def verify_clip(self, file: str) -> dict:
        entry = self._latest(file)
        if entry is not None and entry.get("action") == "removed":
            return {"file": file, "status": "removed", "removed_at": entry["time"],
                    "detail": f"Deleted ({entry.get('reason', 'unknown reason')}); the deletion is on record."}
        if entry is None or entry.get("action") != "sealed":
            return {"file": file, "status": "unsealed",
                    "detail": "This clip was never sealed, so its integrity can't be proven."}
        video = self.recordings / file
        result = {"file": file, "sealed_at": entry["time"], "seq": entry["seq"], "sha256": entry["sha256"],
                  "backfilled": bool(entry.get("backfilled")),
                  "signature_ok": self._signature_ok(entry["hash"], entry["sig"]) and _entry_hash(entry) == entry["hash"]}
        if not video.exists():
            return {**result, "status": "missing", "detail": "The clip is sealed but its file is gone, with no record "
                                                             "of it being deleted."}
        current = file_sha256(video)
        meta = video.with_suffix(".json")
        meta_now = file_sha256(meta) if meta.exists() else None
        result["current_sha256"] = current
        if not result["signature_ok"]:
            return {**result, "status": "tampered", "detail": "The ledger entry for this clip was altered."}
        if current != entry["sha256"]:
            return {**result, "status": "tampered", "detail": "The video file changed after it was sealed."}
        if entry.get("meta_sha256") and meta_now != entry["meta_sha256"]:
            return {**result, "status": "tampered", "detail": "The clip's details (time, camera, level) changed after "
                                                             "it was sealed."}
        return {**result, "status": "verified", "detail": "The video is byte-for-byte identical to when it was sealed."}

    def verify_chain(self) -> dict:
        """Check every entry's hash, signature and link to the entry before it."""
        problems: list[dict] = []
        prev = GENESIS
        entries = self.entries()
        for i, entry in enumerate(entries, start=1):
            if "corrupt" in entry:
                problems.append({"seq": i, "problem": "Unreadable entry"})
                continue
            if entry.get("seq") != i:
                problems.append({"seq": i, "problem": "Entries were removed or reordered"})
            if entry.get("prev") != prev:
                problems.append({"seq": i, "problem": "Does not link to the entry before it"})
            if _entry_hash(entry) != entry.get("hash"):
                problems.append({"seq": i, "problem": "Contents changed after it was written"})
            elif not self._signature_ok(entry["hash"], entry.get("sig", "")):
                problems.append({"seq": i, "problem": "Signature does not match"})
            prev = entry.get("hash", "")
        return {"ok": not problems, "entries": len(entries), "problems": problems[:50], "head": prev}

    def audit(self) -> dict:
        """The chain plus every sealed clip that should still exist."""
        chain = self.verify_chain()
        clips = [self.verify_clip(f) for f in sorted(self.sealed_files())]
        counts: dict[str, int] = {}
        for c in clips:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        unsealed = [v.name for v in self.recordings.glob("*.mp4")
                    if not v.name.endswith(".part.mp4") and v.name not in self.sealed_files()]
        return {"chain": chain, "clips": clips, "counts": counts, "unsealed": sorted(unsealed),
                "ok": chain["ok"] and all(c["status"] == "verified" for c in clips),
                "checked_at": _now_iso()}

    def status(self) -> dict:
        entries = self.entries()
        last = entries[-1] if entries else None
        return {"entries": len(entries), "sealed": len(self.sealed_files()), "fingerprint": self.fingerprint(),
                "last": last, "head": last.get("hash") if last else GENESIS}

    def reset_cache(self) -> None:
        with self._lock:
            self._entries = None


evidence_vault = EvidenceVault()
