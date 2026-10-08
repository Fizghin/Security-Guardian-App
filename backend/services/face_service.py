"""
Insider (known person) recognition with OpenCV's YuNet face detector and
SFace face recogniser. Both are small ONNX models that run on the CPU and are
downloaded once into storage/models/.

Insider photos live in storage/faces/<Name>/<photo>.jpg, with the face
embedding cached next to each photo as <photo>.npy.
"""
import shutil
import threading
import uuid
from pathlib import Path

import cv2
import httpx
import numpy as np

from config import FACES_DIR, MODELS_DIR
from models.domain import Detection

MODEL_FILES = {
    "face_detection_yunet_2023mar.onnx":
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MIN_FACE_PX = 36


class FaceError(ValueError):
    pass


def safe_name(name: str) -> str:
    cleaned = "".join(c for c in (name or "") if c.isalnum() or c in " _-.").strip().strip(".")
    cleaned = " ".join(cleaned.split())[:64]
    if not cleaned:
        raise FaceError("Name must contain letters or numbers")
    return cleaned


class FaceService:
    def __init__(self, faces_dir: Path = FACES_DIR, models_dir: Path = MODELS_DIR):
        self.faces_dir = faces_dir
        self.models_dir = models_dir
        self._lock = threading.RLock()
        self._detector = None
        self._recognizer = None
        self._gallery: dict[str, list[tuple[str, np.ndarray]]] = {}
        self.state = "idle"  # idle | downloading | ready | error
        self.error: str | None = None

    # ---- models --------------------------------------------------------
    def _model_path(self, name: str) -> Path:
        return self.models_dir / name

    def models_present(self) -> bool:
        return all(self._model_path(n).exists() for n in MODEL_FILES)

    def _download_models(self) -> None:
        for name, url in MODEL_FILES.items():
            dest = self._model_path(name)
            if dest.exists():
                continue
            print(f"[faces] Downloading {name}")
            tmp = dest.with_suffix(".part")
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 16):
                        fh.write(chunk)
            tmp.replace(dest)

    def ensure_ready(self) -> bool:
        with self._lock:
            if self._recognizer is not None:
                return True
            try:
                if not self.models_present():
                    self.state = "downloading"
                    self._download_models()
                self._detector = cv2.FaceDetectorYN.create(
                    str(self._model_path("face_detection_yunet_2023mar.onnx")), "", (320, 320), 0.8, 0.3, 20)
                self._recognizer = cv2.FaceRecognizerSF.create(
                    str(self._model_path("face_recognition_sface_2021dec.onnx")), "")
                self.state, self.error = "ready", None
                self._load_gallery()
                print(f"[faces] Ready; {len(self._gallery)} insider(s) enrolled")
                return True
            except Exception as exc:
                self.state = "error"
                self.error = (f"Face models unavailable: {exc}. Place {', '.join(MODEL_FILES)} "
                              f"in {self.models_dir} to enable insider recognition offline.")
                print(f"[faces] {self.error}")
                return False

    def prepare_async(self) -> None:
        threading.Thread(target=self.ensure_ready, daemon=True, name="faces-init").start()

    # ---- embeddings ----------------------------------------------------
    def _largest_face(self, img) -> np.ndarray | None:
        h, w = img.shape[:2]
        self._detector.setInputSize((w, h))
        _, faces = self._detector.detect(img)
        if faces is None or len(faces) == 0:
            return None
        return max(faces, key=lambda f: f[2] * f[3])

    def _embed(self, img, face) -> np.ndarray:
        aligned = self._recognizer.alignCrop(img, face)
        feat = self._recognizer.feature(aligned).flatten().astype(np.float32)
        return feat / (np.linalg.norm(feat) + 1e-9)

    def _photo_embedding(self, photo: Path) -> np.ndarray | None:
        cache = photo.with_suffix(".npy")
        if cache.exists() and cache.stat().st_mtime >= photo.stat().st_mtime:
            return np.load(cache)
        img = cv2.imread(str(photo))
        if img is None:
            return None
        face = self._largest_face(img)
        if face is None:
            return None
        feat = self._embed(img, face)
        np.save(cache, feat)
        return feat

    def _load_gallery(self) -> None:
        gallery = {}
        for person in sorted(p for p in self.faces_dir.iterdir() if p.is_dir()):
            feats = []
            for photo in sorted(person.iterdir()):
                if photo.suffix.lower() in IMAGE_EXTS:
                    feat = self._photo_embedding(photo)
                    if feat is not None:
                        feats.append((photo.name, feat))
            if feats:
                gallery[person.name] = feats
        self._gallery = gallery

    # ---- insiders ------------------------------------------------------
    def list_insiders(self) -> list[dict]:
        out = []
        with self._lock:
            enrolled = {name: {p for p, _ in feats} for name, feats in self._gallery.items()}
        for person in sorted((p for p in self.faces_dir.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
            photos = sorted(p for p in person.iterdir() if p.suffix.lower() in IMAGE_EXTS)
            usable = enrolled.get(person.name, set())
            out.append({
                "name": person.name,
                "added": person.stat().st_ctime,
                "photos": [{"file": p.name, "usable": p.name in usable} for p in photos],
            })
        return out

    def add_photo(self, name: str, data: bytes) -> dict:
        name = safe_name(name)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise FaceError("File is not a readable image")
        h, w = img.shape[:2]
        scale = 1280 / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        if not self.ensure_ready():
            raise FaceError(self.error or "Face recognition is unavailable")
        with self._lock:
            face = self._largest_face(img)
            if face is None:
                raise FaceError("No face found in this photo. Use a clear, front-facing photo")
            if min(face[2], face[3]) < MIN_FACE_PX:
                raise FaceError("Face is too small. Use a closer photo")
            feat = self._embed(img, face)
            person_dir = self.faces_dir / name
            person_dir.mkdir(parents=True, exist_ok=True)
            photo = person_dir / f"{uuid.uuid4().hex[:12]}.jpg"
            cv2.imwrite(str(photo), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            np.save(photo.with_suffix(".npy"), feat)
            self._gallery.setdefault(name, []).append((photo.name, feat))
        return {"name": name, "file": photo.name}

    def photo_path(self, name: str, file: str) -> Path:
        path = (self.faces_dir / safe_name(name) / Path(file).name).resolve()
        if not path.is_file() or path.parent.parent != self.faces_dir.resolve():
            raise FileNotFoundError(file)
        return path

    def delete_photo(self, name: str, file: str) -> None:
        path = self.photo_path(name, file)
        with self._lock:
            path.unlink()
            path.with_suffix(".npy").unlink(missing_ok=True)
            feats = [(p, f) for p, f in self._gallery.get(path.parent.name, []) if p != path.name]
            if feats:
                self._gallery[path.parent.name] = feats
            else:
                self._gallery.pop(path.parent.name, None)
            if not any(path.parent.iterdir()):
                path.parent.rmdir()

    def delete_insider(self, name: str) -> None:
        name = safe_name(name)
        person_dir = self.faces_dir / name
        if not person_dir.is_dir():
            raise FileNotFoundError(name)
        with self._lock:
            shutil.rmtree(person_dir)
            self._gallery.pop(name, None)

    @property
    def enrolled_count(self) -> int:
        return len(self._gallery)

    # ---- live recognition ---------------------------------------------
    def _all_faces(self, frame) -> list[np.ndarray]:
        """Faces in the whole frame (detected on a copy at most 1280 px wide)."""
        h, w = frame.shape[:2]
        scale = min(1.0, 1280 / w)
        img = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
        self._detector.setInputSize((img.shape[1], img.shape[0]))
        _, faces = self._detector.detect(img)
        if faces is None:
            return []
        out = []
        for f in faces:
            f = f.copy()
            f[:14] /= scale  # box + 5 landmarks back to full-frame pixels
            if min(f[2], f[3]) >= MIN_FACE_PX * 0.75:
                out.append(f)
        return out

    def identify(self, frame, detections: list[Detection], threshold: float) -> None:
        """Marks detections as known insiders in place when a face matches."""
        if self._recognizer is None or not self._gallery or not detections:
            return
        with self._lock:
            for face in self._all_faces(frame):
                cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
                # A face belongs to the smallest person box that has it in its upper half
                # (boxes overlap when people stand close together).
                owners = [d for d in detections
                          if d.bbox[0] <= cx <= d.bbox[2] and d.bbox[1] <= cy <= d.bbox[1] + (d.bbox[3] - d.bbox[1]) * 0.5]
                if not owners:
                    continue
                det = min(owners, key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]))
                if det.face_visible:
                    continue
                det.face_visible = True
                feat = self._embed(frame, face)
                best_name, best_score = None, -1.0
                for name, feats in self._gallery.items():
                    score = max(float(np.dot(feat, f)) for _, f in feats)
                    if score > best_score:
                        best_name, best_score = name, score
                if best_name and best_score >= threshold:
                    det.known, det.identity = True, best_name

    def status(self) -> dict:
        return {"state": self.state, "error": self.error, "enrolled": self.enrolled_count}


face_service = FaceService()
