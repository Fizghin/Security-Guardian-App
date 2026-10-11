"""
Model files downloaded on first use into storage/models/, for the features that
listen to phone microphones (sound recognition and the Guard Bot).
"""
from pathlib import Path
from typing import Callable

import httpx


def download(url: str, dest: Path, progress: Callable[[float | None], None] = lambda fraction: None) -> None:
    """Fetches url into dest through a .part file, so an interrupted download is never mistaken for a model.
    progress gets the fraction done, or None when the server doesn't say how big the file is."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60, connect=15)) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 16):
                    fh.write(chunk)
                    done += len(chunk)
                    progress(min(1.0, done / total) if total else None)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
