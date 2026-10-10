import json
import time

import numpy as np
import pytest

from services import recording_service
from services.recording_service import Recorder, RecordingLibrary


@pytest.fixture
def library(tmp_path):
    return RecordingLibrary(tmp_path)


@pytest.fixture
def recorder(library):
    rec = Recorder("porch", lambda: "Porch", library)
    frame = np.zeros((241, 321, 3), np.uint8)  # odd size: encoder needs even dimensions
    frame[:, :, 1] = 128
    rec.configure(preroll_seconds=1, postroll_seconds=0, max_clip_seconds=300)
    rec.start_sampler(lambda: frame)
    yield rec
    rec.shutdown()


def test_records_clip_with_preroll_and_metadata(recorder, library, tmp_path):
    finished = []
    recorder.on_finished = finished.append
    time.sleep(1.2)  # fill the pre-roll buffer
    name = recorder.start("test", level=2)
    assert name and recorder.active and "_porch_" in name
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
    assert meta["max_level"] == 3 and meta["camera_id"] == "porch" and meta["camera"] == "Porch"
    assert meta["duration"] >= 1.5, "pre-roll frames are included"
    assert clip.with_suffix(".jpg").exists()
    assert finished and finished[0]["file"] == name

    listed = library.list()
    assert listed[0]["file"] == name and listed[0]["camera"] == "Porch" and listed[0]["thumbnail"] is True
    assert library.list("porch") and not library.list("garage")
    library.delete(name)
    assert not any(tmp_path.iterdir())


def test_rejects_paths_outside_library(library):
    for bad in ("../settings.json", "..\\x.mp4", "clip.part.mp4", "nope.mp4"):
        with pytest.raises(FileNotFoundError):
            library.path(bad)


def test_cannot_delete_clip_in_progress(recorder, library):
    time.sleep(0.3)
    name = recorder.start("test")
    with pytest.raises(PermissionError):
        library.delete(name)
    recorder.stop(immediate=True)
    library.delete(name)


def test_clean_up_never_takes_a_starting_clip_for_a_leftover(library, monkeypatch):
    library.ffmpeg = None

    class Writer:  # the encoder; clean-up after another camera's clip runs just as the file appears
        codec, playable = "mp4v", False

        def __init__(self, path, size):
            self.path = path
            path.write_bytes(b"video")
            library.prune(30)

        def write(self, frame):
            pass

        def close(self):
            return self.path.exists()

    monkeypatch.setattr(recording_service, "_OpenCVWriter", Writer)
    recorder = Recorder("porch", lambda: "Porch", library)
    recorder.start_sampler(lambda: np.zeros((240, 320, 3), np.uint8))
    try:
        time.sleep(0.2)
        name = recorder.start("test")
        assert recorder.file == name
        time.sleep(0.3)
        recorder.stop(immediate=True)
    finally:
        recorder.shutdown()
    assert recorder.file is None and (library.dir / name).exists()


def test_protected_clips_survive_retention_and_deletion(library, tmp_path):
    import os
    clip = tmp_path / "20200101_000000_cam1_intruder.mp4"
    clip.write_bytes(b"x")
    old = 1_000_000_000
    os.utime(clip, (old, old))
    library.protect(clip.name, True)
    assert library.list()[0]["protected"] is True
    with pytest.raises(PermissionError):
        library.delete(clip.name)
    assert library.prune(30) == 0 and clip.exists()
    library.protect(clip.name, False)
    assert library.list()[0]["protected"] is False
    assert library.prune(30) == 1 and not clip.exists()
