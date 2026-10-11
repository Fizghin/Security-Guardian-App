"""
Property map endpoints: the floorplan, its scale, each camera's calibration, the live map and its
recent history. Like the rest of the dashboard they are behind the dashboard password.
"""
import asyncio
import math
import time

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api import _dashboard_origin, _until_closed
from services.floorplan import (GRID_METRES_PER_PX, MAP_DIR, MAX_PAIRS, MAX_PICTURE_BYTES, CalibrationError,
                                calibrate, clean_pairs, remove_pictures, save_picture)
from services.property_map import HISTORY_SECONDS, HISTORY_STEP, property_map
from services.settings_service import SettingsError, settings_service

map_router = APIRouter()
MAP_PERIOD = 0.2  # the live map is sent 5 times a second
NO_MAP = {"image": "", "width": 0, "height": 0, "metres_per_px": None, "scale_line": []}


def _view() -> dict:
    cfg = settings_service.get()
    m = cfg.map
    calibrations = property_map.calibrations()
    cameras = []
    for c in cfg.cameras:
        cal, problem = calibrations.get(c.id, (None, None))
        cameras.append({"id": c.id, "name": c.name, "enabled": c.enabled, "points": c.map_points,
                        "calibration": cal.to_dict() if cal else None, "problem": problem})
    picture = f"/api/map/picture?v={m.image}" if m.image not in ("", "grid") else None
    return {**m.model_dump(), "picture": picture, "cameras": cameras}


def _save(patch: dict) -> None:
    try:
        settings_service.update(patch)
    except SettingsError as exc:
        raise HTTPException(422, str(exc))


def _new_map(new: dict) -> None:
    """Another floorplan: the cameras' points were placed on the old one."""
    cameras = [{**c.model_dump(), "map_points": []} for c in settings_service.get().cameras]
    _save({"map": new, "cameras": cameras})


@map_router.get("/api/map")
def get_map():
    return _view()


@map_router.get("/api/map/picture")
def map_picture():
    image = settings_service.get().map.image
    path = MAP_DIR / image
    if image in ("", "grid") or not path.is_file():
        raise HTTPException(404, "No floorplan picture")
    # The file name changes with every upload, so the browser may keep it
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@map_router.post("/api/map/picture")
def upload_picture(file: UploadFile = File(...)):
    data = file.file.read(MAX_PICTURE_BYTES + 1)
    try:
        name, width, height = save_picture(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    _new_map({**NO_MAP, "image": name, "width": width, "height": height})
    remove_pictures(keep=name)
    return _view()


class GridRequest(BaseModel):
    width_m: float = Field(30, ge=5, le=400)
    height_m: float = Field(20, ge=5, le=400)


@map_router.post("/api/map/grid")
def use_grid(req: GridRequest):
    _new_map({**NO_MAP, "image": "grid", "width": round(req.width_m / GRID_METRES_PER_PX),
              "height": round(req.height_m / GRID_METRES_PER_PX), "metres_per_px": GRID_METRES_PER_PX})
    remove_pictures()
    return _view()


@map_router.delete("/api/map")
def remove_map():
    _new_map(NO_MAP)
    remove_pictures()
    return _view()


def _on_map(x: float, y: float) -> bool:
    m = settings_service.get().map
    return 0 <= x <= m.width and 0 <= y <= m.height


class ScaleRequest(BaseModel):
    line: list[float] = Field(..., min_length=4, max_length=4)  # [x1, y1, x2, y2] in map pixels
    metres: float = Field(..., ge=0.1, le=10000)


@map_router.post("/api/map/scale")
def set_scale(req: ScaleRequest):
    if not settings_service.get().map.image:
        raise HTTPException(409, "Upload a floorplan or use the grid first")
    x1, y1, x2, y2 = req.line
    if not (_on_map(x1, y1) and _on_map(x2, y2)):
        raise HTTPException(422, "Both points must be on the map")
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 10:
        raise HTTPException(422, "Pick two points further apart")
    _save({"map": {"metres_per_px": req.metres / length,
                   "scale_line": [round(v, 1) for v in req.line] + [req.metres]}})
    return _view()


class PointsRequest(BaseModel):
    # [picture x, picture y, map x, map y]: picture in 0..1, map in pixels. Empty takes the camera off the map.
    points: list[list[float]] = Field(default_factory=list, max_length=MAX_PAIRS)


def _calibrate(camera_id: str, points: list[list[float]]):
    cfg = settings_service.get()
    if cfg.camera(camera_id) is None:
        raise HTTPException(404, "Camera not found")
    if not cfg.map.image:
        raise HTTPException(409, "Upload a floorplan or use the grid first")
    if not cfg.map.metres_per_px:
        raise HTTPException(409, "Set the map's scale first")
    try:
        pairs = clean_pairs(points)
        if not pairs:
            raise CalibrationError("Pick at least 4 pairs of points")
        if not all(_on_map(p[2], p[3]) for p in pairs):
            raise CalibrationError("Points on the map must lie inside it")
        return pairs, calibrate(pairs, cfg.map.metres_per_px)
    except ValueError as exc:  # also CalibrationError
        raise HTTPException(422, str(exc))


@map_router.post("/api/map/cameras/{camera_id}/check")
def check_calibration(camera_id: str, req: PointsRequest):
    """How well the pairs fit and the camera's ground area on the map, without saving."""
    return _calibrate(camera_id, req.points)[1].to_dict()


@map_router.put("/api/map/cameras/{camera_id}")
def save_calibration(camera_id: str, req: PointsRequest):
    pairs = _calibrate(camera_id, req.points)[0] if req.points else []
    try:
        settings_service.update_camera(camera_id, {"map_points": pairs})
    except KeyError:
        raise HTTPException(404, "Camera not found")
    except SettingsError as exc:
        raise HTTPException(422, str(exc))
    return _view()


@map_router.get("/api/map/history")
def map_history(minutes: float = Query(10, gt=0, le=HISTORY_SECONDS / 60)):
    """Where people were, one sample per person per second: [time, id, x, y, status, label]."""
    now = time.time()
    return {"time": now, "step": HISTORY_STEP, "samples": property_map.history(minutes, now)}


@map_router.websocket("/ws/map")
async def ws_map(websocket: WebSocket):
    # Where people are is private, so like talk and listen only the dashboard's own pages may ask
    if not _dashboard_origin(websocket):
        await websocket.close(1008)
        return
    await websocket.accept()
    closed = asyncio.create_task(_until_closed(websocket))
    try:
        while not closed.done():
            await websocket.send_json(property_map.snapshot(time.time()))
            await asyncio.wait([closed], timeout=MAP_PERIOD)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass  # the dashboard went away mid-send
    finally:
        closed.cancel()
