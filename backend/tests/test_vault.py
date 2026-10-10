import hashlib
import json
import os
import sys
import threading
import time

import numpy as np
import pytest
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from models.database import init_db, utcnow
from services.event_service import EventService
from services.recording_service import Recorder, RecordingLibrary
from services.vault_service import GENESIS, EvidenceVault, canonical, describe_check


class Events:
    def __init__(self):
        self.entries = []

    def log(self, event_type, description, severity="INFO", **kwargs):
        self.entries.append((event_type, description, severity))


@pytest.fixture
def dirs(tmp_path):
    rec, snap = tmp_path / "recordings", tmp_path / "snapshots"
    rec.mkdir()
    snap.mkdir()
    return rec, snap


@pytest.fixture
def vault(tmp_path, dirs):
    return EvidenceVault(tmp_path / "vault", recordings_dir=dirs[0], snapshots_dir=dirs[1], events=Events())


def clip(rec, name="20261010_031245_cam1_intruder.mp4", data=b"video-bytes" * 100, meta=None):
    path = rec / name
    path.write_bytes(data)
    path.with_suffix(".json").write_text(json.dumps(meta or {"camera": "Porch", "duration": 12.0}))
    return path


def picture(snap, name="20261010-031245-000001_detection.jpg", data=b"\xff\xd8jpeg\xff\xd9"):
    path = snap / name
    path.write_bytes(data)
    return path


def ledger(vault) -> list[str]:
    return vault.ledger_path.read_text().splitlines()


def sealed(vault, *paths):
    for p in paths:
        (vault.seal_clip if p.suffix == ".mp4" else vault.seal_picture)(p)
    assert vault.wait_idle()


def test_key_is_created_once_and_kept_private(vault, tmp_path):
    status = vault.status()
    assert len(status["fingerprint"]) == 64 and status["entries"] == 0
    if sys.platform != "win32":
        assert vault.private_path.stat().st_mode & 0o777 == 0o600
    public = load_pem_public_key(vault.public_path.read_bytes())
    assert public is not None and b"PRIVATE" not in vault.public_pem
    again = EvidenceVault(vault.dir, *vault.folders.values())
    assert again.status()["fingerprint"] == status["fingerprint"], "the key survives a restart"


def test_sealing_never_waits_for_the_disk(vault, dirs):
    gate = threading.Event()
    vault._submit(None, gate.wait)  # the vault's thread is busy
    started = time.monotonic()
    vault.seal_clip(clip(dirs[0]))
    assert time.monotonic() - started < 0.1 and not vault.ledger_path.exists()
    gate.set()
    assert vault.wait_idle() and len(ledger(vault)) == 1


def test_recorder_seals_each_clip_when_it_is_saved(vault, dirs):
    library = RecordingLibrary(dirs[0])
    vault.start(library=library)
    recorder = Recorder("porch", lambda: "Porch", library)
    recorder.configure(preroll_seconds=1, postroll_seconds=0, max_clip_seconds=300)
    recorder.start_sampler(lambda: np.zeros((240, 320, 3), np.uint8))
    try:
        time.sleep(0.5)
        name = recorder.start("test", level=2)
        time.sleep(0.5)
        recorder.stop()
    finally:
        recorder.shutdown()
    assert vault.wait_idle()
    [entry] = [json.loads(line) for line in ledger(vault)]
    path = dirs[0] / name
    assert entry["kind"] == "clip" and entry["name"] == name and entry["seq"] == 1 and entry["prev"] == GENESIS
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() and entry["size"] == path.stat().st_size
    assert entry["meta_sha256"] == hashlib.sha256(path.with_suffix(".json").read_bytes()).hexdigest()
    assert "sealed_late" not in entry
    assert vault.verify("clip", name)["status"] == "intact"
    assert vault.seal_info("clip", name)["sealed_at"] == entry["time"]


def test_entries_are_chained_and_signed(vault, dirs):
    sealed(vault, clip(dirs[0]), picture(dirs[1]), picture(dirs[1], "b.jpg"))
    lines = ledger(vault)
    public = load_pem_public_key(vault.public_pem)
    prev = GENESIS
    for n, line in enumerate(lines, 1):
        entry = json.loads(line)
        assert entry["seq"] == n and entry["prev"] == prev
        assert set(entry) >= {"seq", "time", "kind", "name", "sha256", "size", "meta_sha256", "prev", "sig"}
        public.verify(bytes.fromhex(entry["sig"]), canonical(entry))
        prev = hashlib.sha256(line.encode()).hexdigest()
    assert [json.loads(line)["kind"] for line in lines] == ["clip", "picture", "picture"]
    sealed(vault, dirs[1] / "b.jpg")
    assert len(ledger(vault)) == 3, "a file is sealed once"


def test_files_saved_without_the_vault_are_sealed_late(vault, dirs):
    old = clip(dirs[0])
    (dirs[0] / "20261010_040000_cam1_intruder.part.mp4").write_bytes(b"still recording")
    pic = picture(dirs[1])
    vault.start()
    assert vault.wait_idle()
    entries = [json.loads(line) for line in ledger(vault)]
    assert {(e["kind"], e["name"]) for e in entries} == {("clip", old.name), ("picture", pic.name)}
    assert all(e["sealed_late"] for e in entries)
    assert vault.verify("clip", old.name)["sealed_late"] is True
    assert vault.events.entries[-1][0] == "VAULT" and "sealed late" in vault.events.entries[-1][1]
    restarted = EvidenceVault(vault.dir, *vault.folders.values())
    restarted.start()
    assert restarted.wait_idle() and len(ledger(restarted)) == 2, "nothing is sealed twice"


def test_deleting_adds_an_entry_and_keeps_the_chain(vault, dirs, tmp_path):
    init_db()
    library = RecordingLibrary(dirs[0])
    events = EventService(dirs[1])
    vault.start(library=library, events=events)
    kept, deleted, expired = clip(dirs[0], "a.mp4"), clip(dirs[0], "b.mp4"), clip(dirs[0], "c.mp4")
    sealed(vault, kept, deleted, expired)
    name = events._save_snapshot(utcnow(), "DETECTION", b"jpeg")
    assert vault.wait_idle() and vault.known("picture", name)

    library.delete("b.mp4")
    old = time.time() - 40 * 86400
    os.utime(expired, (old, old))
    assert library.prune(30) == 1
    os.utime(dirs[1] / name, (old, old))
    events.prune_snapshots(0)  # no event refers to the picture
    assert vault.wait_idle()

    removals = [json.loads(line) for line in ledger(vault)][4:]
    assert [(e["kind"], e["deleted_kind"], e["name"]) for e in removals] == [
        ("deleted", "clip", "b.mp4"), ("deleted", "clip", "c.mp4"), ("deleted", "picture", name)]
    assert removals[0]["reason"] == "Deleted from the dashboard"
    assert removals[1]["reason"] == "Older than 30 days (automatic clean-up)"
    assert removals[2]["reason"] == "No event refers to it"
    gone = vault.verify("clip", "b.mp4")
    assert gone["status"] == "missing" and gone["deleted"]["reason"] == "Deleted from the dashboard"
    assert vault.verify("clip", "a.mp4")["status"] == "intact"
    summary = vault.verify_all()
    assert summary["broken_at"] is None and summary["intact"] == 1 and summary["missing_count"] == 0
    assert summary["deleted"] == 3


def test_verify_outcomes(vault, dirs):
    a, b, c = clip(dirs[0], "a.mp4"), clip(dirs[0], "b.mp4"), picture(dirs[1], "c.jpg")
    sealed(vault, a, b, c)
    assert vault.verify("clip", "a.mp4")["status"] == "intact"
    assert vault.verify("picture", "c.jpg")["status"] == "intact"

    data = bytearray(a.read_bytes())
    data[5] ^= 0x01  # one flipped bit
    a.write_bytes(bytes(data))
    result = vault.verify("clip", "a.mp4")
    assert result["status"] == "modified" and result["changed"] == "file"
    assert result["current_sha256"] != result["sha256"]

    b.with_suffix(".json").write_text(json.dumps({"camera": "Garage"}))
    assert vault.verify("clip", "b.mp4")["changed"] == "details"

    c.unlink()
    result = vault.verify("picture", "c.jpg")
    assert result["status"] == "missing" and result["deleted"] is None

    clip(dirs[0], "never.mp4")
    assert vault.verify("clip", "never.mp4")["status"] == "not_sealed"
    with pytest.raises(ValueError):
        vault.verify("clip", "../settings.json")


def test_an_edited_ledger_line_breaks_the_chain_from_there(vault, dirs):
    a, b = clip(dirs[0], "a.mp4"), clip(dirs[0], "b.mp4")
    sealed(vault, a, b, clip(dirs[0], "c.mp4"))
    assert vault.verify("clip", "b.mp4")["status"] == "intact"  # remembers the checked part
    b.write_bytes(b"replaced video")
    lines = ledger(vault)
    forged = json.loads(lines[1])
    forged["sha256"], forged["size"] = hashlib.sha256(b"replaced video").hexdigest(), len(b"replaced video")
    lines[1] = json.dumps(forged, sort_keys=True, separators=(",", ":"))
    vault.ledger_path.write_text("\n".join(lines) + "\n")
    assert vault.verify("clip", "a.mp4")["status"] == "intact", "entries before the edit still check"
    for name in ("b.mp4", "c.mp4"):
        result = vault.verify("clip", name)
        assert result["status"] == "ledger_broken" and result["broken_at"] == 2


def test_reordered_lines_break_the_chain(vault, dirs):
    sealed(vault, clip(dirs[0], "a.mp4"), clip(dirs[0], "b.mp4"))
    first, second = ledger(vault)
    vault.ledger_path.write_text(f"{second}\n{first}\n")
    result = vault.verify("clip", "a.mp4")
    assert result["status"] == "ledger_broken" and result["broken_at"] == 1
    assert vault.verify_all()["broken_at"] == 1


def test_removed_last_line_is_noticed(vault, dirs):
    sealed(vault, clip(dirs[0], "a.mp4"), clip(dirs[0], "b.mp4"))
    first, _ = ledger(vault)
    vault.ledger_path.write_text(f"{first}\n")
    assert vault.verify("clip", "a.mp4")["status"] == "intact"
    result = vault.verify("clip", "b.mp4")
    assert result["status"] == "ledger_broken" and result["broken_at"] == 2


def test_partial_last_line_from_a_crash_is_dropped(vault, dirs):
    sealed(vault, clip(dirs[0], "a.mp4"))
    with open(vault.ledger_path, "a") as f:
        f.write('{"seq":2,"kind":"cl')
    again = EvidenceVault(vault.dir, *vault.folders.values())
    sealed(again, clip(dirs[0], "b.mp4"))
    assert [json.loads(line)["seq"] for line in ledger(again)] == [1, 2]
    assert again.verify_all()["broken_at"] is None


def test_full_check_summary_runs_in_the_background(vault, dirs):
    a, b = clip(dirs[0], "a.mp4"), clip(dirs[0], "b.mp4")
    sealed(vault, a, b, clip(dirs[0], "c.mp4"))
    a.write_bytes(b"changed")
    b.unlink()
    clip(dirs[0], "new.mp4")
    assert vault.start_full_check()
    for _ in range(100):
        if not vault.checking:
            break
        time.sleep(0.05)
    summary = vault.last_check
    assert summary["files"] == 3 and summary["intact"] == 1
    assert summary["modified"] == ["a.mp4"] and summary["missing"] == ["b.mp4"] and summary["not_sealed"] == ["new.mp4"]
    assert json.loads(vault.check_path.read_text()) == summary, "kept across restarts"
    kind, text, severity = vault.events.entries[-1]
    assert kind == "VAULT" and severity == "HIGH" and text == describe_check(summary)
    assert "1 modified after sealing" in text and "1 missing" in text
