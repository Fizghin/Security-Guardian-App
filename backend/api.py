import asyncio
import time
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from services.ai_service import AIError, ai_service
from services.brain_service import brain_service
from services.detection_service import detection_service
from services.event_service import event_service
from services.face_service import FaceError, face_service
from services.notification_service import notification_service
from services.pipeline_service import pipeline_service
from services.recording_service import recording_service
from services.settings_service import SettingsError, settings_service
from services.siren_service import siren_service
from services.system_service import system_stats
from services.tts_service import tts_service
from services.video_service import scan_local_cameras, video_service

router = APIRouter(prefix="/api")
ws_router = APIRouter()

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


# ---- live state --------------------------------------------------------------
@router.get("/health")
def health():
    return {"ok": True, "time": time.time()}


@router.get("/status")
def status():
    return {
        **brain_service.status(),
        "camera": {**video_service.status(), "name": settings_service.get().camera.name},
        "pipeline": pipeline_service.status(),
        "detector": detection_service.status(),
        "recording": recording_service.status(),
        "siren": siren_service.status(),
        "voice": tts_service.status(),
        "ai": ai_service.status(),
        "faces": face_service.status(),
        "server_time": time.time(),
    }


class ArmRequest(BaseModel):
    armed: bool


@router.post("/arm")
def arm(req: ArmRequest):
    brain_service.set_armed(req.armed)
    return {"armed": brain_service.armed}


@router.post("/panic")
def panic():
    brain_service.trigger_panic()
    return {"ok": True}


@router.post("/alarm/reset")
def reset_alarm():
    return {"ok": True, "was_active": brain_service.reset_alarm()}


class TestRequest(BaseModel):
    seconds: int = Field(30, ge=5, le=120)


@router.post("/test-intrusion")
def test_intrusion(req: TestRequest):
    if not video_service.status()["connected"]:
        raise HTTPException(409, "A test needs a working camera feed. Check the camera settings.")
    if not brain_service.armed:
        raise HTTPException(409, "Arm the system first; a disarmed system ignores people on camera.")
    pipeline_service.simulate(req.seconds)
    event_service.log("TEST", f"Test intrusion started for {req.seconds}s", "INFO")
    return {"ok": True, "seconds": req.seconds}


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)


@router.post("/speak")
def speak(req: SpeakRequest):
    if tts_service.available is False:
        raise HTTPException(409, f"No speech engine on this computer: {tts_service.error}")
    return {"ok": brain_service.speak(req.text)}


# ---- events --------------------------------------------------------------------
def _event_filters(type, severity, since, until, search):
    return {"event_type": type or None, "severity": severity or None, "since": since or None,
            "until": until or None, "search": search or None}


@router.get("/events")
def list_events(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                type: str = "", severity: str = "", since: str = "", until: str = "", search: str = ""):
    try:
        return event_service.query(limit=limit, offset=offset, **_event_filters(type, severity, since, until, search))
    except ValueError as exc:
        raise HTTPException(422, f"Bad filter: {exc}")


@router.get("/events/summary")
def events_summary(hours: int = Query(24, ge=1, le=24 * 365)):
    return event_service.summary(hours)


@router.get("/events/export.csv")
def export_events(type: str = "", severity: str = "", since: str = "", until: str = "", search: str = ""):
    csv_text = event_service.export_csv(**_event_filters(type, severity, since, until, search))
    name = f"guardian-events-{datetime.now():%Y%m%d-%H%M}.csv"
    return Response(csv_text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/events")
def clear_events():
    return {"deleted": event_service.clear()}


# ---- recordings ----------------------------------------------------------------
@router.get("/recordings")
def list_recordings():
    return {"items": recording_service.list(), "usage_bytes": recording_service.usage_bytes(),
            "recording": recording_service.status()}


@router.get("/recordings/{name}")
def get_recording(name: str, download: bool = False):
    try:
        path = recording_service.path(name)
    except FileNotFoundError:
        raise HTTPException(404, "Recording not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


@router.get("/recordings/{name}/thumbnail")
def get_thumbnail(name: str):
    try:
        return FileResponse(recording_service.path(name, ".jpg"), media_type="image/jpeg")
    except FileNotFoundError:
        raise HTTPException(404, "Thumbnail not found")


@router.delete("/recordings/{name}")
def delete_recording(name: str):
    try:
        recording_service.delete(name)
    except FileNotFoundError:
        raise HTTPException(404, "Recording not found")
    except PermissionError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


# ---- insiders --------------------------------------------------------------------
@router.get("/insiders")
def list_insiders():
    return {"items": face_service.list_insiders(), "faces": face_service.status(),
            "enabled": settings_service.get().detection.face_recognition}


@router.post("/insiders")
async def add_insider(name: str = Form(...), files: list[UploadFile] = File(...)):
    results = []
    for upload in files:
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            results.append({"file": upload.filename, "ok": False, "error": "File is larger than 15 MB"})
            continue
        try:
            saved = await run_in_threadpool(face_service.add_photo, name, data)
            results.append({"file": upload.filename, "ok": True, "error": None, "name": saved["name"]})
        except FaceError as exc:
            results.append({"file": upload.filename, "ok": False, "error": str(exc)})
    added = [r for r in results if r["ok"]]
    if added:
        event_service.log("INSIDER", f"Added {len(added)} photo(s) for {added[0]['name']}", "INFO")
    return {"results": results}


@router.get("/insiders/{name}/photos/{file}")
def get_insider_photo(name: str, file: str):
    try:
        return FileResponse(face_service.photo_path(name, file))
    except (FileNotFoundError, FaceError):
        raise HTTPException(404, "Photo not found")


@router.delete("/insiders/{name}/photos/{file}")
def delete_insider_photo(name: str, file: str):
    try:
        face_service.delete_photo(name, file)
    except (FileNotFoundError, FaceError):
        raise HTTPException(404, "Photo not found")
    return {"ok": True}


@router.delete("/insiders/{name}")
def delete_insider(name: str):
    try:
        face_service.delete_insider(name)
    except (FileNotFoundError, FaceError):
        raise HTTPException(404, "Insider not found")
    event_service.log("INSIDER", f"Removed insider {name}", "INFO")
    return {"ok": True}


# ---- settings --------------------------------------------------------------------
@router.get("/settings")
def get_settings():
    return settings_service.public()


@router.patch("/settings")
def patch_settings(patch: dict):
    patch.pop("armed", None)  # arming goes through /api/arm so it is logged
    if isinstance(patch.get("ai"), dict):
        patch["ai"].pop("api_key_set", None)
    try:
        settings_service.update(patch)
    except SettingsError as exc:
        raise HTTPException(422, str(exc))
    return settings_service.public()


@router.get("/ai/models")
def ai_models(provider: str = "", base_url: str = "", api_key: str = ""):
    cfg = settings_service.get().ai
    overrides = {k: v for k, v in {"provider": provider, "base_url": base_url, "api_key": api_key}.items() if v}
    if overrides:
        cfg = cfg.model_copy(update=overrides)
    try:
        return {"models": ai_service.list_models(cfg), "error": None}
    except AIError as exc:
        return {"models": [], "error": str(exc)}


class AITestRequest(BaseModel):
    level: int = Field(1, ge=1, le=4)
    speak: bool = False


@router.post("/ai/test")
async def ai_test(req: AITestRequest):
    result = await run_in_threadpool(ai_service.generate_warning, req.level, 12)
    if req.speak:
        result["spoken"] = tts_service.say(result["text"], settings_service.get().ai.voice_rate, interrupt=True)
    return result


@router.get("/cameras/scan")
async def scan_cameras():
    active = video_service.active_index
    found = await run_in_threadpool(scan_local_cameras, 6, {active} if active is not None else None)
    if active is not None:
        st = video_service.status()
        found.append({"index": active, "width": st["width"], "height": st["height"], "in_use": True})
    return {"cameras": sorted(found, key=lambda c: c["index"])}


@router.get("/system")
def system():
    return {
        **system_stats(),
        "detector": detection_service.status(),
        "pipeline": pipeline_service.status(),
        "recording_encoder": recording_service.status()["encoder"],
        "recordings_bytes": recording_service.usage_bytes(),
        "voice": tts_service.status(),
        "siren": siren_service.status(),
        "faces": face_service.status(),
        "notifications": notification_service.status(),
    }


@router.get("/notifications")
def notifications_status():
    return notification_service.status()


@router.post("/notifications/test")
async def notifications_test():
    status = notification_service.status()
    if not (status["discord"] or status["email"]):
        raise HTTPException(409, "No notification channel configured. Set DISCORD_WEBHOOK_URL or SMTP_* in backend/.env")
    return {"results": await run_in_threadpool(notification_service.send_test)}


# ---- video -------------------------------------------------------------------------
@router.get("/snapshot.jpg")
def snapshot():
    jpeg = pipeline_service.snapshot()
    if jpeg is None:
        raise HTTPException(503, "No video frame available")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/stream.mjpg")
async def mjpeg_stream():
    async def frames():
        last = -1
        while True:
            jpeg, frame_id = pipeline_service.latest_jpeg()
            if jpeg is not None and frame_id != last:
                last = frame_id
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            else:
                await asyncio.sleep(0.02)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@ws_router.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    await websocket.accept()
    last = -1
    try:
        while True:
            jpeg, frame_id = pipeline_service.latest_jpeg()
            if jpeg is not None and frame_id != last:
                last = frame_id
                await websocket.send_bytes(jpeg)
            else:
                await asyncio.sleep(0.02)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass  # the dashboard went away mid-send
