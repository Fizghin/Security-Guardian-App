import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from main import app
from init_db import init_db
from services.forensics_service import ForensicsService
from services.recording_service import RecordingLibrary


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def test_library(tmp_path):
    return RecordingLibrary(directory=tmp_path)


@pytest.fixture
def mock_recording(tmp_path):
    clip = tmp_path / "20261010_120000_frontdoor_test.mp4"
    clip.write_bytes(b"dummy mp4 video content data")
    meta = {
        "reason": "test",
        "started": "2026-10-10T12:00:00+00:00",
        "duration": 15.5,
        "max_level": 3,
        "codec": "h264",
        "playable": True,
        "camera_id": "frontdoor",
        "camera": "Front Door",
    }
    meta_file = tmp_path / "20261010_120000_frontdoor_test.json"
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    return clip.name


def test_verify_clip_integrity_fresh_and_tampered(test_library, mock_recording):
    service = ForensicsService(library=test_library, key=b"secretkey01234567890123456789012")

    # First verification generates cryptographic receipt
    res1 = service.verify_clip_integrity(mock_recording)
    assert res1["verified"] is True
    assert res1["tampered"] is False
    assert len(res1["sha256_hash"]) == 64
    assert len(res1["signature"]) == 64

    # Second verification verifies signature
    res2 = service.verify_clip_integrity(mock_recording)
    assert res2["verified"] is True
    assert res2["tampered"] is False

    # Simulate video file tampering
    clip_path = test_library.path(mock_recording)
    clip_path.write_bytes(b"TAMPERED VIDEO CONTENT DATA")

    res3 = service.verify_clip_integrity(mock_recording)
    assert res3["verified"] is False
    assert res3["tampered"] is True


def test_generate_forensic_digest(test_library, mock_recording):
    service = ForensicsService(library=test_library, key=b"secretkey01234567890123456789012")
    digest = service.generate_forensic_digest(mock_recording)

    assert digest["file"] == mock_recording
    assert digest["camera"] == "Front Door"
    assert digest["threat_level_peak"] == 3
    assert digest["duration_seconds"] == 15.5
    assert len(digest["key_findings"]) >= 3
    assert len(digest["timeline"]) >= 1


def test_forensics_api_endpoints(client, test_library, mock_recording, monkeypatch):
    import services.recording_service as rs
    import services.forensics_service as fs
    import api

    monkeypatch.setattr(rs, "recording_library", test_library)
    monkeypatch.setattr(fs, "recording_library", test_library)
    monkeypatch.setattr(fs.forensics_service, "library", test_library)
    monkeypatch.setattr(api, "recording_library", test_library)

    # API verify integrity
    res_verify = client.get(f"/api/recordings/{mock_recording}/verify-integrity")
    assert res_verify.status_code == 200
    data_verify = res_verify.json()
    assert data_verify["verified"] is True
    assert data_verify["tampered"] is False

    # API forensic digest
    res_digest = client.get(f"/api/recordings/{mock_recording}/forensic-digest")
    assert res_digest.status_code == 200
    data_digest = res_digest.json()
    assert data_digest["camera"] == "Front Door"
    assert data_digest["threat_level_peak"] == 3

    # API 404
    res_404 = client.get("/api/recordings/nonexistent.mp4/verify-integrity")
    assert res_404.status_code == 404
