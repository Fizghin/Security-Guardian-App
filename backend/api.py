import asyncio
import base64
import json
import math
import time
from datetime import datetime
from urllib.parse import urlsplit

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from config import BACKEND_DIR, DEV_ORIGINS, PHONE_PORT, PHONES_ENABLED, PUBLIC_URL
from services.ai_service import AIError, WarningContext, ai_service
from services.camera_access import local_camera_blocked
from services.camera_service import camera_manager
from services.detection_service import detection_service
from services.event_service import event_service
from services.face_service import FaceError, face_service
from services.notification_service import ChannelError, notification_service
from services.phone_service import lan_addresses, pairing_urls, phone_hub, qr_svg
from services.recording_service import recording_library
from services.routine_service import routine_service
from services.schedule_service import time_zone
from services.settings_service import PHONE_SOURCE, NotificationSettings, SettingsError, new_token, settings_service
from services.siren_service import siren_service
from services.sources import grab_test_frame, parse_source, scan_local_cameras
from services.system_service import system_stats
from services.talk_service import TalkError, audio_hub, talk_player, talk_player_status
from services.tts_service import tts_service
from services.visitor_service import visitor_service

router = APIRouter(prefix="/api")
ws_router = APIRouter()
phone_router = APIRouter()

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PHONE_FRAME = 4 * 1024 * 1024
PHONE_PAGE = BACKEND_DIR / "phone" / "index.html"


def _unit(camera_id: str):
    try:
        return camera_manager.get(camera_id)
    except KeyError:
        raise HTTPException(404, "Camera not found or turned off")


def _jpeg_data_url(frame, quality=80) -> str:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if ok else ""


# ---- live state --------------------------------------------------------------
@router.get("/health")
def health():
    return {"ok": True, "app": "guardian", "time": time.time()}


@router.get("/status")
def status():
    return {
        **camera_manager.status(),
        "detector": detection_service.status(),
        "voice": tts_service.status(),
        "siren": siren_service.status(),
        "ai": ai_service.status(),
        "faces": face_service.status(),
        "phone": {"enabled": PHONES_ENABLED, "port": PHONE_PORT},
        "talk": talk_player_status(),
        "schedule": camera_manager.schedule_status(),
        "server_time": time.time(),
        "time_zone": time_zone(),
    }


class ArmRequest(BaseModel):
    armed: bool


@router.post("/arm")
def arm(req: ArmRequest):
    camera_manager.set_armed(req.armed)
    return {"armed": settings_service.get().armed}


@router.post("/panic")
def panic():
    camera_manager.panic()
    return {"ok": True}


@router.post("/alarm/reset")
def reset_alarm():
    return {"ok": True, "was_active": camera_manager.reset_alarm()}


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)
    camera_id: str | None = None


@router.post("/speak")
def speak(req: SpeakRequest):
    if req.camera_id:
        _unit(req.camera_id)
        if audio_hub.relay(req.camera_id).talking:
            raise HTTPException(409, "Someone is talking through this camera right now")
    if not camera_manager.speak(req.text, req.camera_id):
        detail = tts_service.error if tts_service.available is False else None
        raise HTTPException(409, "Nothing could play it: no speech engine on this computer"
                                 + (f" ({detail})" if detail else "") + " and no phone speaker online")
    return {"ok": True}


# ---- cameras ------------------------------------------------------------------
class CameraCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    source: str = Field(..., min_length=1, max_length=500)
    audio: str | None = None


class CameraUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    source: str | None = Field(None, min_length=1, max_length=500)
    enabled: bool | None = None
    audio: str | None = None
    zones: list[list[list[float]]] | None = None
    zones_aspect: float | None = None  # width / height of the picture the zones were drawn on


def _camera_view(cfg) -> dict:
    data = cfg.model_dump()
    data["kind"] = parse_source(cfg.source)[0]
    if not cfg.is_phone:
        data.pop("token")
    return data


@router.get("/cameras")
def list_cameras():
    return {"cameras": [_camera_view(c) for c in settings_service.get().cameras],
            "phone": {"enabled": PHONES_ENABLED, "port": PHONE_PORT, "addresses": lan_addresses()}}


@router.post("/cameras")
def add_camera(req: CameraCreate):
    if req.source == PHONE_SOURCE and not PHONES_ENABLED:
        raise HTTPException(409, "Phone cameras are turned off (PHONE_PORT=0 and no PUBLIC_URL in backend/.env)")
    try:
        cam = settings_service.add_camera(req.name, req.source, req.audio)
    except SettingsError as exc:
        raise HTTPException(422, str(exc))
    event_service.log("SYSTEM", f"Camera added ({'phone' if cam.is_phone else cam.source})", camera=cam.name)
    return _camera_view(cam)


@router.patch("/cameras/{camera_id}")
def update_camera(camera_id: str, req: CameraUpdate):
    changes = req.model_dump(exclude_none=True)
    if "zones" in changes and "zones_aspect" not in changes:  # assume they were drawn on the live picture
        unit = camera_manager.units.get(camera_id)
        changes["zones_aspect"] = unit.picture_aspect() if unit else None
    try:
        cam = settings_service.update_camera(camera_id, changes)
    except KeyError:
        raise HTTPException(404, "Camera not found")
    except SettingsError as exc:
        raise HTTPException(422, str(exc))
    return _camera_view(cam)


@router.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str):
    cam = settings_service.get().camera(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    settings_service.remove_camera(camera_id)
    event_service.log("SYSTEM", "Camera removed", camera=cam.name)
    return {"ok": True}


@router.post("/cameras/{camera_id}/reset-link")
def reset_phone_link(camera_id: str):
    cam = settings_service.get().camera(camera_id)
    if cam is None or not cam.is_phone:
        raise HTTPException(404, "Phone camera not found")
    return _camera_view(settings_service.update_camera(camera_id, {"token": new_token()}))


@router.get("/cameras/{camera_id}/pairing")
def phone_pairing(camera_id: str, address: str = ""):
    cam = settings_service.get().camera(camera_id)
    if cam is None or not cam.is_phone:
        raise HTTPException(404, "Phone camera not found")
    urls = pairing_urls(cam.token)
    if address:
        urls = sorted(urls, key=lambda u: address not in u)
    return {"urls": urls, "qr_svg": qr_svg(urls[0]) if urls else None, "port": PHONE_PORT, "public_url": PUBLIC_URL}


class SourceTest(BaseModel):
    source: str = Field(..., min_length=1, max_length=500)


@router.post("/cameras/test")
async def test_source(req: SourceTest):
    frame, error = await run_in_threadpool(grab_test_frame, req.source)
    if frame is None:
        return {"ok": False, "error": error}
    h, w = frame.shape[:2]
    return {"ok": True, "width": w, "height": h, "preview": _jpeg_data_url(cv2.resize(frame, (480, int(h * 480 / w))))}


@router.get("/cameras/scan")
async def scan_cameras():
    if blocked := local_camera_blocked():
        raise HTTPException(409, blocked)
    in_use = {u.source.active_index for u in camera_manager.units.values()
              if getattr(u.source, "active_index", None) is not None}
    found = await run_in_threadpool(scan_local_cameras, 6, in_use)
    for index in sorted(in_use):
        found.append({"index": index, "width": 0, "height": 0, "in_use": True})
    return {"cameras": sorted(found, key=lambda c: c["index"])}


class TestRequest(BaseModel):
    seconds: int = Field(30, ge=5, le=120)


@router.post("/cameras/{camera_id}/test-intrusion")
def test_intrusion(camera_id: str, req: TestRequest):
    unit = _unit(camera_id)
    if not unit.connected:
        raise HTTPException(409, "A test needs this camera's picture. Check that it is connected.")
    if not settings_service.get().armed:
        raise HTTPException(409, "Arm the system first; a disarmed system ignores people on camera.")
    unit.simulate(req.seconds)
    event_service.log("TEST", f"Test intrusion started for {req.seconds}s", camera=unit.name())
    return {"ok": True, "seconds": req.seconds}


@router.get("/cameras/{camera_id}/snapshot.jpg")
def snapshot(camera_id: str, raw: bool = False):
    """raw: the picture without boxes and zones, e.g. for drawing zones on it."""
    unit = _unit(camera_id)
    jpeg = unit.raw_snapshot() if raw else unit.snapshot()
    if jpeg is None:
        raise HTTPException(503, "No picture from this camera yet")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/cameras/{camera_id}/stream.mjpg")
async def mjpeg_stream(camera_id: str):
    unit = _unit(camera_id)

    async def frames():
        last = -1
        while True:
            jpeg, frame_id = unit.latest_jpeg()
            if jpeg is not None and frame_id != last:
                last = frame_id
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            else:
                await asyncio.sleep(0.02)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/cameras/{camera_id}/faces")
async def faces_on_camera(camera_id: str):
    unit = _unit(camera_id)
    try:
        faces = await run_in_threadpool(face_service.faces_in_frame, camera_id, unit.latest_raw())
    except FaceError as exc:
        raise HTTPException(409, str(exc))
    return {"faces": faces}


# ---- learned routine ------------------------------------------------------------------
def _camera_config(camera_id: str):
    cam = settings_service.get().camera(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    return cam


@router.get("/cameras/{camera_id}/routine")
def camera_routine(camera_id: str):
    """When people are usually in view of this camera, as learned so far."""
    cam = _camera_config(camera_id)
    report = routine_service.get(cam.id).report(time.time())
    return {"camera_id": cam.id, "name": cam.name, "mode": settings_service.get().learning.unusual_activity, **report}


@router.delete("/cameras/{camera_id}/routine")
def reset_camera_routine(camera_id: str):
    cam = _camera_config(camera_id)
    routine_service.reset(cam.id)
    event_service.log("SYSTEM", "Learned routine reset: learning starts again", camera=cam.name)
    return {"ok": True}


async def _until_closed(websocket: WebSocket) -> None:
    # For sockets the dashboard never sends anything on: returns when it leaves or the server stops.
    # Without it a camera with nothing to send would never notice and keep its handler alive forever.
    try:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass


@ws_router.websocket("/ws/stream/{camera_id}")
async def ws_stream(websocket: WebSocket, camera_id: str):
    await websocket.accept()
    closed = asyncio.create_task(_until_closed(websocket))
    last = -1
    try:
        while not closed.done():
            unit = camera_manager.units.get(camera_id)
            jpeg, frame_id = unit.latest_jpeg() if unit else (None, -1)
            if jpeg is not None and frame_id != last:
                last = frame_id
                await websocket.send_bytes(jpeg)
            else:
                await asyncio.sleep(0.02)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass  # the dashboard went away mid-send
    finally:
        closed.cancel()


# ---- live audio: talk through a camera, listen to a phone ----------------------------
def _dashboard_origin(websocket: WebSocket) -> bool:
    """Browsers let any website open a WebSocket to this computer, and the dashboard has no password by
    default. Talk and listen only accept the dashboard's own pages, so no other site can use a camera's
    microphone or speaker. Clients that aren't browsers send no Origin."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    public = urlsplit(PUBLIC_URL)
    allowed = {f"{public.scheme}://{public.netloc}"} if PUBLIC_URL else set()
    for host in (websocket.headers.get("host"), websocket.headers.get("x-forwarded-host")):
        if host:
            allowed |= {f"http://{host}", f"https://{host}"}
    return origin in allowed or origin in DEV_ORIGINS


async def _refuse(websocket: WebSocket, message: str, kind: str = "error") -> None:
    await websocket.send_json({"type": kind, "message": message})
    await websocket.close()


@ws_router.websocket("/ws/talk/{camera_id}")
async def ws_talk(websocket: WebSocket, camera_id: str):
    """The dashboard sends the owner's voice as 16 kHz 16-bit mono PCM while they hold Talk."""
    if not _dashboard_origin(websocket):
        await websocket.close(1008)
        return
    await websocket.accept()
    cam = settings_service.get().camera(camera_id)
    if cam is None or not cam.enabled:
        await _refuse(websocket, "Camera not found or turned off")
        return
    relay = audio_hub.relay(camera_id)
    try:
        talk = relay.start_talk(cam.audio, talk_player)
    except TalkError as exc:
        await _refuse(websocket, str(exc), "busy" if exc.busy else "error")
        return
    try:
        await websocket.send_json({"type": "ready", **talk.describe()})
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                talk.feed(message["bytes"])
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        seconds = relay.end_talk(talk)
        if seconds >= 0.5:
            await run_in_threadpool(event_service.log, "TALK", f"Spoke through {cam.name} for {max(1, round(seconds))} s",
                                    camera=cam.name)


@ws_router.websocket("/ws/listen/{camera_id}")
async def ws_listen(websocket: WebSocket, camera_id: str):
    """Sends the phone's microphone (16 kHz 16-bit mono PCM) while the dashboard listens."""
    if not _dashboard_origin(websocket):
        await websocket.close(1008)
        return
    await websocket.accept()
    cam = settings_service.get().camera(camera_id)
    if cam is None or not cam.enabled or not cam.is_phone:
        await _refuse(websocket, "Only phone cameras have a microphone to listen to")
        return
    relay = audio_hub.relay(camera_id)
    chunks = relay.add_listener()
    closed = asyncio.create_task(_until_closed(websocket))
    try:
        await websocket.send_json({"type": "ready", "mic": relay.mic_live()})
        while not closed.done():
            try:
                chunk = await asyncio.wait_for(chunks.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            await websocket.send_bytes(chunk)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        closed.cancel()
        relay.remove_listener(chunks)


# ---- phones (also reachable on the HTTPS phone port) ------------------------------
@phone_router.get("/phone", response_class=HTMLResponse)
def phone_page():
    return HTMLResponse(PHONE_PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


def _phone_link(k: str):
    link = phone_hub.authenticate(k)
    if link is None:
        raise HTTPException(401, "Unknown or expired pairing link")
    return link


def _phone_reply(link) -> dict:
    return {"commands": phone_hub.take_commands(link), "config": settings_service.get().phone.model_dump()}


@phone_router.get("/api/phone/hello")
def phone_hello(k: str = ""):
    link = _phone_link(k)
    cam = settings_service.get().camera(link.camera_id)
    return {"name": cam.name if cam else "Guardian camera", "config": settings_service.get().phone.model_dump()}


def _decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


@phone_router.post("/api/phone/frame")
async def phone_frame(request: Request, k: str = ""):
    link = _phone_link(k)
    body = await request.body()
    if not body or len(body) > MAX_PHONE_FRAME:
        raise HTTPException(413, "Frame missing or too large")
    frame = await run_in_threadpool(_decode, body)
    if frame is None:
        raise HTTPException(400, "Not a JPEG image")
    link.source.push(frame)
    h = request.headers
    link.source.touch({
        "battery": int(h["x-battery"]) if h.get("x-battery", "").isdigit() else None,
        "charging": h.get("x-charging") == "1" if "x-charging" in h else None,
        "camera": h.get("x-camera"),
        "user_agent": (h.get("user-agent") or "")[:160],
        "address": request.client.host if request.client else None,
        "state": "streaming",
    })
    return _phone_reply(link)


class Heartbeat(BaseModel):
    state: str = "idle"
    battery: int | None = None
    charging: bool | None = None


@phone_router.post("/api/phone/heartbeat")
def phone_heartbeat(beat: Heartbeat, request: Request, k: str = ""):
    link = _phone_link(k)
    link.source.touch({"state": beat.state, "battery": beat.battery, "charging": beat.charging,
                       "user_agent": (request.headers.get("user-agent") or "")[:160],
                       "address": request.client.host if request.client else None})
    return _phone_reply(link)


@phone_router.websocket("/api/phone/audio")
async def phone_audio(websocket: WebSocket, k: str = ""):
    """The phone's audio link. Phone to server: {"type": "level", "db": peak dBFS} about 4 times a second
    while its microphone is on, and microphone PCM while asked to. Server to phone: {"type": "listen",
    "on": bool} and the owner's voice as PCM (both 16 kHz 16-bit mono)."""
    await websocket.accept()
    link = phone_hub.authenticate(k)
    if link is None:
        await websocket.close(4401)  # the page stops retrying with this link
        return
    relay = audio_hub.relay(link.camera_id)
    phone = relay.connect_phone()

    async def deliver() -> None:
        try:
            # Checked again every 2 s too, so a revoked phone that sends nothing stops hearing the owner
            while phone_hub.authenticate(k) is link:
                try:
                    message = await asyncio.wait_for(phone.queue.get(), 2)
                except asyncio.TimeoutError:
                    continue
                if message is None:
                    await websocket.close(4000)  # the same page connected again
                    return
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_json(message)
            await websocket.close(4401)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass  # the phone went away mid-send

    sender = asyncio.create_task(deliver())
    try:
        while not sender.done():
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if phone_hub.authenticate(k) is not link:  # a new pairing link was made, or the camera removed
                await websocket.close(4401)
                break
            if message.get("bytes"):
                relay.phone_audio(message["bytes"])
            elif message.get("text") and len(message["text"]) < 200:
                try:
                    data = json.loads(message["text"])
                    db = float(data["db"]) if data.get("type") == "level" else math.nan
                    if math.isfinite(db):
                        relay.phone_level(max(-100.0, min(0.0, db)))
                except (ValueError, TypeError, KeyError, AttributeError):
                    pass
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        sender.cancel()
        relay.disconnect_phone(phone)


# ---- events ------------------------------------------------------------------------
def _event_filters(type, severity, since, until, search, camera):
    return {"event_type": type or None, "severity": severity or None, "since": since or None,
            "until": until or None, "search": search or None, "camera": camera or None}


@router.get("/events")
def list_events(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), type: str = "",
                severity: str = "", since: str = "", until: str = "", search: str = "", camera: str = ""):
    try:
        return event_service.query(limit=limit, offset=offset,
                                   **_event_filters(type, severity, since, until, search, camera))
    except ValueError as exc:
        raise HTTPException(422, f"Bad filter: {exc}")


@router.get("/events/summary")
def events_summary(hours: int = Query(24, ge=1, le=24 * 365)):
    return event_service.summary(hours)


@router.get("/events/export.csv")
def export_events(type: str = "", severity: str = "", since: str = "", until: str = "", search: str = "",
                  camera: str = ""):
    csv_text = event_service.export_csv(**_event_filters(type, severity, since, until, search, camera))
    name = f"guardian-events-{datetime.now():%Y%m%d-%H%M}.csv"
    return Response(csv_text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/events/{event_id}/snapshot.jpg")
def event_snapshot(event_id: int, v: str = ""):
    """v: the picture's file name from the event. Each file gets its own URL, which browsers may cache."""
    path = event_service.snapshot_path(event_id)
    if path is None or (v and v != path.name):
        raise HTTPException(404, "This event has no picture")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/events")
def clear_events():
    return {"deleted": event_service.clear()}


# ---- recordings --------------------------------------------------------------------
@router.get("/recordings")
def list_recordings(camera: str = ""):
    active = [{"camera": u.name(), **u.recorder.status()} for u in camera_manager.units.values() if u.recorder.active]
    return {"items": recording_library.list(camera or None), "usage_bytes": recording_library.usage_bytes(),
            "active": active, "encoder": recording_library.encoder}


@router.get("/recordings/{name}")
def get_recording(name: str, download: bool = False):
    try:
        path = recording_library.path(name)
    except FileNotFoundError:
        raise HTTPException(404, "Recording not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


@router.get("/recordings/{name}/thumbnail")
def get_thumbnail(name: str):
    try:
        return FileResponse(recording_library.path(name, ".jpg"), media_type="image/jpeg")
    except FileNotFoundError:
        raise HTTPException(404, "Thumbnail not found")


@router.delete("/recordings/{name}")
def delete_recording(name: str):
    try:
        recording_library.delete(name)
    except FileNotFoundError:
        raise HTTPException(404, "Recording not found")
    except PermissionError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


# ---- insiders ----------------------------------------------------------------------
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
            results.append({"file": upload.filename, "ok": True, "error": None, "name": saved["name"],
                            "warning": saved["warning"]})
        except FaceError as exc:
            results.append({"file": upload.filename, "ok": False, "error": str(exc)})
    added = [r for r in results if r["ok"]]
    if added:
        event_service.log("INSIDER", f"Added {len(added)} photo(s) for {added[0]['name']}", "INFO")
    return {"results": results}


class CaptureRequest(BaseModel):
    camera_id: str
    index: int = Field(..., ge=0)
    name: str = Field(..., min_length=1, max_length=64)


@router.post("/insiders/capture")
async def capture_insider(req: CaptureRequest):
    try:
        saved = await run_in_threadpool(face_service.capture_face, req.camera_id, req.index, req.name)
    except FaceError as exc:
        raise HTTPException(409, str(exc))
    event_service.log("INSIDER", f"Added a photo for {saved['name']} from the camera", "INFO")
    return saved


@router.post("/insiders/check")
async def check_insider_photo(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 15 MB")
    threshold = settings_service.get().detection.face_match_threshold
    try:
        return {"faces": await run_in_threadpool(face_service.check_photo, data, threshold), "threshold": threshold}
    except FaceError as exc:
        raise HTTPException(409, str(exc))


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


# ---- visitors ----------------------------------------------------------------------
@router.get("/visitors")
def list_visitors(repeat: bool = False, camera: str = "", search: str = ""):
    """repeat: only people seen on more than one visit."""
    det = settings_service.get().detection
    return {"items": visitor_service.list(repeat, camera, search),
            "enabled": det.face_recognition and det.remember_visitors,
            "retention_days": det.visitor_retention_days, "visit_gap_minutes": det.visit_gap_minutes,
            "faces": face_service.status()}


@router.get("/visitors/{visitor_id}")
def get_visitor(visitor_id: int):
    try:
        return visitor_service.get(visitor_id)
    except KeyError:
        raise HTTPException(404, "Visitor not found")


@router.get("/visitors/{visitor_id}/photos/{n}.jpg")
def get_visitor_photo(visitor_id: int, n: int, v: str = ""):
    """n: the face's place among the visitor's best (0 = best). v: its file name, which makes the URL
    unique to that picture so browsers may cache it."""
    try:
        path = visitor_service.photo_path(visitor_id, n, v)
    except FileNotFoundError:
        raise HTTPException(404, "Photo not found")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400" if v else "no-cache"})


class VisitorUpdate(BaseModel):
    label: str | None = Field(None, max_length=40)
    note: str | None = Field(None, max_length=300)


@router.patch("/visitors/{visitor_id}")
def update_visitor(visitor_id: int, req: VisitorUpdate):
    try:
        return visitor_service.update(visitor_id, req.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(404, "Visitor not found")


class MakeInsiderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


@router.post("/visitors/{visitor_id}/make-insider")
async def make_insider(visitor_id: int, req: MakeInsiderRequest):
    try:
        return await run_in_threadpool(visitor_service.make_insider, visitor_id, req.name)
    except KeyError:
        raise HTTPException(404, "Visitor not found")
    except FaceError as exc:
        raise HTTPException(409, str(exc))


@router.delete("/visitors/{visitor_id}")
def forget_visitor(visitor_id: int):
    try:
        visitor_service.forget(visitor_id)
    except KeyError:
        raise HTTPException(404, "Visitor not found")
    return {"ok": True}


@router.delete("/visitors")
def forget_all_visitors():
    return {"deleted": visitor_service.forget_all()}


# ---- settings ----------------------------------------------------------------------
@router.get("/settings")
def get_settings():
    return settings_service.public()


@router.patch("/settings")
def patch_settings(patch: dict):
    patch.pop("armed", None)    # arming goes through /api/arm so it is logged
    patch.pop("cameras", None)  # cameras have their own endpoints
    if isinstance(patch.get("ai"), dict):
        patch["ai"].pop("api_key_set", None)
    if isinstance(patch.get("notifications"), dict):
        notifications = patch["notifications"]
        notifications.pop("configured", None)
        for key in [k for k in notifications if k.endswith("_set")]:
            notifications.pop(key)
        # "" removes a secret; a stray space typed into its field leaves it alone
        for key in NotificationSettings.SECRETS:
            value = notifications.get(key)
            if isinstance(value, str) and value and not value.strip():
                notifications.pop(key)
    try:
        settings_service.update(patch)
    except SettingsError as exc:
        raise HTTPException(422, str(exc))
    if "schedule" in patch:
        camera_manager.check_schedule()  # act on an edited schedule now rather than at the next check
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
    esc = settings_service.get().escalation
    ctx = WarningContext(level=req.level, location="Test", recording=req.level >= esc.record_at_level,
                         siren=esc.siren_enabled and req.level >= esc.siren_at_level,
                         siren_next=esc.siren_enabled and esc.siren_at_level == req.level + 1)
    result = await run_in_threadpool(ai_service.generate_warning, ctx)
    if req.speak:
        result["spoken"] = tts_service.say(result["text"], settings_service.get().ai.voice_rate, interrupt=True)
    return result


# ---- system ------------------------------------------------------------------------
@router.get("/system")
def system():
    return {
        **system_stats(),
        "detector": detection_service.status(),
        "recording_encoder": recording_library.encoder,
        "recordings_bytes": recording_library.usage_bytes(),
        "voice": tts_service.status(),
        "siren": siren_service.status(),
        "talk": talk_player_status(),
        "faces": face_service.status(),
        "notifications": notification_service.status(),
        "ai": ai_service.status(),
        "phone": {"enabled": PHONES_ENABLED, "port": PHONE_PORT, "addresses": lan_addresses(), "public_url": PUBLIC_URL},
    }


@router.get("/notifications")
def notifications_status():
    return notification_service.status()


@router.post("/notifications/test")
async def notifications_test():
    if not notification_service.status()["any"]:
        raise HTTPException(409, "No alert channel is set up yet. Fill in one below and save first.")
    return {"results": await run_in_threadpool(notification_service.send_test)}


class TelegramChatsRequest(BaseModel):
    token: str = ""  # a token typed in but not saved yet; empty uses the saved one


@router.post("/notifications/telegram/chats")
async def telegram_chats(body: TelegramChatsRequest):
    # A POST, so the token stays out of URLs that proxies and tunnels log
    try:
        return {"chats": await run_in_threadpool(notification_service.telegram_chats, body.token)}
    except ChannelError as exc:
        raise HTTPException(502, str(exc))
