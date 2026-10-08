"""
Insider (known person) recognition with OpenCV's YuNet face detector and
SFace face recogniser. Both are small ONNX models that run on the CPU and are
downloaded once into storage/models/.

Insider photos live in storage/faces/<Name>/<photo>.jpg, with the face
embedding cached next to each photo as <photo>.npy.

Per frame, analyze() returns FaceEvidence for each person box; the tracker
turns repeated evidence into a stable identity.

Quality gates were calibrated on SFace: strangers score about 0.0-0.2 against
an insider, the same person 0.7-0.95 at 25+ px faces, but heavy blur pulls a
true match down to about 0.4, so blurry, tiny, strongly turned or low-confidence
faces only count as weak evidence.
"""
import base64
import shutil
import threading
import time
import uuid
from pathlib import Path

import cv2
import httpx
import numpy as np

from config import FACES_DIR, MODELS_DIR
from models.domain import Detection
from services.tracker import FaceEvidence

MODEL_FILES = {
    "face_detection_yunet_2023mar.onnx":
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

DETECT_SCORE = 0.7      # YuNet candidates below this are ignored entirely
QUALITY_SCORE = 0.85    # ...and below this they are weak evidence
QUALITY_MIN_PX = 32     # face side in camera pixels
QUALITY_MAX_YAW = 0.8   # 0 = frontal, ~1 = full profile
QUALITY_MIN_SHARPNESS = 15.0
AMBIGUITY_MARGIN = 0.06  # best insider must beat the runner-up by this much
ENROL_MIN_PX = 48


class FaceError(ValueError):
    pass


def safe_name(name: str) -> str:
    cleaned = "".join(c for c in (name or "") if c.isalnum() or c in " _-.").strip().strip(".")
    cleaned = " ".join(cleaned.split())[:64]
    if not cleaned:
        raise FaceError("Name must contain letters or numbers")
    return cleaned


def _jpeg_data_url(img, quality=85) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""


class FaceService:
    def __init__(self, faces_dir: Path = FACES_DIR, models_dir: Path = MODELS_DIR):
        self.faces_dir = faces_dir
        self.models_dir = models_dir
        self._lock = threading.RLock()
        self._detector = None
        self._recognizer = None
        self._gallery: dict[str, list[tuple[str, np.ndarray]]] = {}
        self._captures: dict[str, tuple[float, np.ndarray, list]] = {}
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
                    str(self._model_path("face_detection_yunet_2023mar.onnx")), "", (320, 320), DETECT_SCORE, 0.3, 50)
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

    @property
    def active(self) -> bool:
        """True when recognition can actually tell insiders apart."""
        return self._recognizer is not None and bool(self._gallery)

    # ---- primitives (call with the lock held) ----------------------------
    def _detect(self, img) -> list[np.ndarray]:
        h, w = img.shape[:2]
        self._detector.setInputSize((w, h))
        _, faces = self._detector.detect(img)
        return [] if faces is None else [f for f in faces]

    def _embed(self, img, face) -> tuple[np.ndarray, float]:
        aligned = self._recognizer.alignCrop(img, face)
        sharpness = float(cv2.Laplacian(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
        feat = self._recognizer.feature(aligned).flatten().astype(np.float32)
        return feat / (np.linalg.norm(feat) + 1e-9), sharpness

    @staticmethod
    def _yaw(face) -> float:
        re, le, nose = face[4:6], face[6:8], face[8:10]
        eyes = float(np.linalg.norm(re - le))
        return abs(float(np.linalg.norm(nose - re) - np.linalg.norm(nose - le))) / max(eyes, 1.0)

    def _quality(self, face, sharpness: float, px_scale: float = 1.0) -> tuple[bool, str | None]:
        size = min(face[2], face[3]) / px_scale
        if face[14] < QUALITY_SCORE:
            return False, "face not clearly visible"
        if size < QUALITY_MIN_PX:
            return False, "face too small"
        if self._yaw(face) > QUALITY_MAX_YAW:
            return False, "face turned away"
        if sharpness < QUALITY_MIN_SHARPNESS:
            return False, "face too blurry"
        return True, None

    def _match(self, feat: np.ndarray, threshold: float) -> tuple[str | None, float, float]:
        """Returns (name or None, best score, runner-up score)."""
        scores = sorted(((max(float(np.dot(feat, f)) for _, f in feats), name)
                         for name, feats in self._gallery.items()), reverse=True)
        if not scores:
            return None, 0.0, 0.0
        best, name = scores[0]
        second = scores[1][0] if len(scores) > 1 else 0.0
        if best >= threshold and (best - second >= AMBIGUITY_MARGIN or second < threshold):
            return name, best, second
        return None, best, second

    # ---- gallery ---------------------------------------------------------
    def _photo_embedding(self, photo: Path) -> np.ndarray | None:
        cache = photo.with_suffix(".npy")
        if cache.exists() and cache.stat().st_mtime >= photo.stat().st_mtime:
            return np.load(cache)
        img = cv2.imread(str(photo))
        if img is None:
            return None
        faces = self._detect(img)
        if not faces:
            return None
        feat, _ = self._embed(img, max(faces, key=lambda f: f[2] * f[3]))
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

    def _save_photo(self, name: str, img, feat: np.ndarray) -> dict:
        others = {n: f for n, f in self._gallery.items() if n != name}
        warning = None
        if others:
            score, lookalike = max((max(float(np.dot(feat, x)) for _, x in fs), n) for n, fs in others.items())
            if score >= 0.5:
                warning = f"This face looks a lot like {lookalike} ({score:.2f}). Check it is the right person"
        person_dir = self.faces_dir / name
        person_dir.mkdir(parents=True, exist_ok=True)
        photo = person_dir / f"{uuid.uuid4().hex[:12]}.jpg"
        cv2.imwrite(str(photo), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        np.save(photo.with_suffix(".npy"), feat)
        self._gallery.setdefault(name, []).append((photo.name, feat))
        return {"name": name, "file": photo.name, "warning": warning}

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
            faces = sorted(self._detect(img), key=lambda f: f[2] * f[3], reverse=True)
            if not faces or faces[0][14] < QUALITY_SCORE:
                raise FaceError("No clear face found. Use a well-lit, front-facing photo")
            face = faces[0]
            if len(faces) > 1 and faces[1][2] * faces[1][3] > 0.4 * face[2] * face[3]:
                raise FaceError("More than one face in this photo. Crop it to just this person")
            if min(face[2], face[3]) < ENROL_MIN_PX:
                raise FaceError("Face is too small. Use a closer photo")
            if self._yaw(face) > QUALITY_MAX_YAW:
                raise FaceError("Face is turned away. Use a photo looking towards the camera")
            feat, sharpness = self._embed(img, face)
            if sharpness < QUALITY_MIN_SHARPNESS:
                raise FaceError("Photo is too blurry")
            return self._save_photo(name, img, feat)

    # ---- enrol from a live camera -----------------------------------------
    def faces_in_frame(self, camera_id: str, frame) -> list[dict]:
        """Faces visible right now, with thumbnails, cached briefly for capture_face()."""
        if frame is None:
            raise FaceError("This camera has no picture right now")
        if not self.ensure_ready():
            raise FaceError(self.error or "Face recognition is unavailable")
        with self._lock:
            faces = sorted((f for f in self._detect(frame) if f[14] >= QUALITY_SCORE),
                           key=lambda f: f[2] * f[3], reverse=True)[:8]
            self._captures[camera_id] = (time.time(), frame.copy(), faces)
            out = []
            for i, face in enumerate(faces):
                feat, sharpness = self._embed(frame, face)
                ok, reason = self._quality(face, sharpness)
                if ok and min(face[2], face[3]) < ENROL_MIN_PX:
                    ok, reason = False, "face too small; step closer to the camera"
                name, score, _ = self._match(feat, 0.36)
                out.append({"index": i, "thumbnail": _jpeg_data_url(self._crop(frame, face, 1.2, 160)),
                            "usable": ok, "reason": reason, "match": name, "score": round(score, 2)})
            return out

    @staticmethod
    def _crop(img, face, margin: float, min_side: int = 0):
        x, y, w, h = (float(v) for v in face[:4])
        cx, cy, side = x + w / 2, y + h / 2, max(w, h) * (1 + 2 * margin)
        H, W = img.shape[:2]
        x1, y1 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        x2, y2 = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
        crop = img[y1:y2, x1:x2]
        if min_side and min(crop.shape[:2]) < min_side:
            f = min_side / min(crop.shape[:2])
            crop = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        return crop

    def capture_face(self, camera_id: str, index: int, name: str) -> dict:
        name = safe_name(name)
        with self._lock:
            cached = self._captures.get(camera_id)
            if not cached or time.time() - cached[0] > 300:
                raise FaceError("That snapshot expired. Take a new one")
            _, frame, faces = cached
            if not 0 <= index < len(faces):
                raise FaceError("Face not found in the snapshot")
            face = faces[index]
            feat, sharpness = self._embed(frame, face)
            ok, reason = self._quality(face, sharpness)
            if not ok or min(face[2], face[3]) < ENROL_MIN_PX:
                raise FaceError(f"Can't use this face: {reason or 'face too small; step closer to the camera'}")
            # Keep a generous crop (not the whole frame) as the stored photo.
            return self._save_photo(name, self._crop(frame, face, 0.6), feat)

    def check_photo(self, data: bytes, threshold: float) -> list[dict]:
        """Who does Guardian think is in this photo? Used to verify enrolment."""
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise FaceError("File is not a readable image")
        if not self.ensure_ready():
            raise FaceError(self.error or "Face recognition is unavailable")
        with self._lock:
            out = []
            for face in sorted(self._detect(img), key=lambda f: f[2] * f[3], reverse=True)[:8]:
                feat, sharpness = self._embed(img, face)
                ok, reason = self._quality(face, sharpness)
                name, best, second = self._match(feat, threshold)
                out.append({"thumbnail": _jpeg_data_url(self._crop(img, face, 0.3, 120)), "match": name,
                            "score": round(best, 2), "runner_up": round(second, 2), "quality_ok": ok, "reason": reason})
            return out

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

    # ---- live recognition --------------------------------------------------
    def analyze(self, frame, detections: list[Detection], threshold: float) -> list[FaceEvidence]:
        """Face evidence for each person box (same order as detections)."""
        evidence = [FaceEvidence() for _ in detections]
        if not self.active or not detections:
            return evidence
        fh, fw = frame.shape[:2]
        with self._lock:
            # Pass 1: whole frame (at most 1280 px wide), faces assigned to the smallest
            # person box that has the face in its upper half (people overlap).
            scale = min(1.0, 1280 / fw)
            small = cv2.resize(frame, (int(fw * scale), int(fh * scale))) if scale < 1 else frame
            for face in self._detect(small):
                face = face.copy()
                face[:14] /= scale
                cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
                owners = [i for i, d in enumerate(detections)
                          if d.bbox[0] <= cx <= d.bbox[2] and d.bbox[1] <= cy <= d.bbox[1] + (d.bbox[3] - d.bbox[1]) * 0.5]
                if not owners:
                    continue
                i = min(owners, key=lambda k: (detections[k].bbox[2] - detections[k].bbox[0])
                        * (detections[k].bbox[3] - detections[k].bbox[1]))
                if not evidence[i].face_visible:
                    evidence[i] = self._evidence(frame, face, threshold)

            # Pass 2: people far from the camera. Their faces can be too small for pass 1,
            # so look again in an enlarged crop of the head area.
            for i, d in enumerate(detections):
                if evidence[i].face_visible:
                    continue
                x1, y1, x2, y2 = d.bbox
                bw, bh = x2 - x1, y2 - y1
                cx1, cy1 = int(max(0, x1 - bw * 0.1)), int(max(0, y1 - bh * 0.05))
                cx2, cy2 = int(min(fw, x2 + bw * 0.1)), int(min(fh, y1 + bh * 0.45))
                if cx2 - cx1 < 24 or cy2 - cy1 < 24:
                    continue
                crop = frame[cy1:cy2, cx1:cx2]
                up = min(3.0, max(1.0, 240 / max(1, cx2 - cx1)))
                if up > 1.05:
                    crop = cv2.resize(crop, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
                faces = self._detect(crop)
                if faces:
                    evidence[i] = self._evidence(crop, max(faces, key=lambda f: f[2] * f[3]), threshold, px_scale=up)
        return evidence

    def _evidence(self, img, face, threshold: float, px_scale: float = 1.0) -> FaceEvidence:
        feat, sharpness = self._embed(img, face)
        ok, _ = self._quality(face, sharpness, px_scale)
        name, score, _ = self._match(feat, threshold if ok else threshold + 0.05)
        return FaceEvidence(face_visible=True, quality_ok=ok, name=name, score=score)

    def status(self) -> dict:
        return {"state": self.state, "error": self.error, "enrolled": self.enrolled_count}


face_service = FaceService()
