import json

import pytest

from services.evidence_service import EvidenceVault
from services.recording_service import RecordingLibrary


@pytest.fixture
def setup(tmp_path):
    recs = tmp_path / "recordings"
    recs.mkdir()
    vault = EvidenceVault(tmp_path / "evidence", recs)
    library = RecordingLibrary(recs)
    library.on_saved = vault.seal
    library.on_removed = vault.record_removal
    return vault, library, recs


def clip(recs, name="20260101_120000_cam1_intruder.mp4", data=b"video-bytes" * 100):
    path = recs / name
    path.write_bytes(data)
    path.with_suffix(".json").write_text(json.dumps({"camera": "Porch", "reason": "intruder"}))
    return path


def test_seal_and_verify(setup):
    vault, _, recs = setup
    path = clip(recs)
    entry = vault.seal(path)
    assert entry["seq"] == 1 and entry["prev"] == "0" * 64 and len(entry["sha256"]) == 64
    result = vault.verify_clip(path.name)
    assert result["status"] == "verified" and result["signature_ok"]
    assert vault.verify_chain()["ok"]
    assert vault.sealed_files() == {path.name}


def test_detects_changed_video_and_details(setup):
    vault, _, recs = setup
    path = clip(recs)
    vault.seal(path)
    path.write_bytes(b"video-bytes" * 99 + b"edited-byt")
    assert vault.verify_clip(path.name)["status"] == "tampered"
    path.write_bytes(b"video-bytes" * 100)
    assert vault.verify_clip(path.name)["status"] == "verified"
    path.with_suffix(".json").write_text(json.dumps({"camera": "Garage", "reason": "intruder"}))
    assert vault.verify_clip(path.name)["status"] == "tampered"


def test_missing_vs_deleted(setup):
    vault, library, recs = setup
    a, b = clip(recs, "a_cam1_intruder.mp4"), clip(recs, "b_cam1_intruder.mp4")
    vault.seal(a)
    vault.seal(b)
    a.unlink()  # gone without a trace
    library.delete(b.name)  # deleted through Guardian: on record
    assert vault.verify_clip(a.name)["status"] == "missing"
    assert vault.verify_clip(b.name)["status"] == "removed"
    audit = vault.audit()
    assert audit["chain"]["ok"] and not audit["ok"] and audit["counts"] == {"missing": 1}


def test_chain_detects_edited_ledger(setup):
    vault, _, recs = setup
    for n in range(3):
        vault.seal(clip(recs, f"{n}_cam1_intruder.mp4", bytes([n]) * 50))
    lines = vault.ledger.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["sha256"] = "f" * 64  # someone "fixes" the record to match an edited video
    lines[1] = json.dumps(entry, sort_keys=True)
    vault.ledger.write_text("\n".join(lines) + "\n")
    vault.reset_cache()
    chain = vault.verify_chain()
    assert not chain["ok"] and chain["problems"][0]["seq"] == 2
    del lines[0]  # removing an entry breaks the links
    vault.ledger.write_text("\n".join(lines) + "\n")
    vault.reset_cache()
    assert any("removed or reordered" in p["problem"] for p in vault.verify_chain()["problems"])


def test_signatures_survive_restart_and_backfill(setup, tmp_path):
    vault, _, recs = setup
    vault.seal(clip(recs, "old_cam1_intruder.mp4"))
    clip(recs, "older_cam1_intruder.mp4", b"x" * 10)
    again = EvidenceVault(tmp_path / "evidence", recs)
    assert again.fingerprint() == vault.fingerprint()
    assert again.seal_unsealed() == 1 and again.seal_unsealed() == 0
    assert again.verify_clip("older_cam1_intruder.mp4")["backfilled"] is True
    assert again.verify_chain()["ok"] and again.verify_chain()["entries"] == 2
    assert "BEGIN PUBLIC KEY" in again.public_key_pem()


def test_unsealed_clip(setup):
    vault, _, recs = setup
    path = clip(recs)
    assert vault.verify_clip(path.name)["status"] == "unsealed"
    assert vault.audit()["unsealed"] == [path.name]
