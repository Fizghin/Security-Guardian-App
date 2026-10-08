import json
import time

import numpy as np
import pytest

from services.recording_service import RecordingService


@pytest.fixture
def recorder(tmp_path):
    rec = RecordingService(tmp_path)
    frame = np.zeros((241, 321, 3), np.uint8)  # odd size: encoder needs even dimensions
    frame[:, :, 1] = 128
    rec.configure(preroll_seconds=1, postroll_seconds=0, max_clip_seconds=300)
    rec.start_sampler(lambda: frame)
    yield rec
    rec.shutdown()


def test_records_clip_with_preroll_and_metadata(recorder, tmp_path):
    finished = []
    recorder.on_finished = finished.append
    time.sleep(1.2)  # fill the pre-roll buffer
    name = recorder.start("test", level=2)
    assert name and recorder.active
    recorder.note_level(3)
    time.sleep(1.0)
    recorder.stop()
    assert not recorder.active
    for _ in range(50):
        if finished:
            break
        time.sleep(0.1)

    clip = tmp_path / name
    assert clip.exists() and clip.stat().st_size > 0
    assert not list(tmp_path.glob("*.part.mp4"))
    meta = json.loads(clip.with_suffix(".json").read_text())
    assert meta["max_level"] == 3
    assert meta["duration"] >= 1.5, "pre-roll frames are included"
    assert clip.with_suffix(".jpg").exists()
    assert finished and finished[0]["file"] == name

    listed = recorder.list()
    assert listed[0]["file"] == name and listed[0]["thumbnail"] is True
    recorder.delete(name)
    assert not any(tmp_path.iterdir())


def test_rejects_paths_outside_library(recorder):
    for bad in ("../settings.json", "..\\x.mp4", "clip.part.mp4", "nope.mp4"):
        with pytest.raises(FileNotFoundError):
            recorder.path(bad)


def test_cannot_delete_clip_in_progress(recorder):
    time.sleep(0.3)
    name = recorder.start("test")
    with pytest.raises(PermissionError):
        recorder.delete(name)
    recorder.stop(immediate=True)
    recorder.delete(name)
