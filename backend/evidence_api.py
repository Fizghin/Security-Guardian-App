"""Dashboard endpoints for the evidence vault and incident reports (behind the dashboard password)."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from services.camera_service import camera_manager
from services.incident_service import incident_service
from services.recording_service import recording_library
from services.vault_service import evidence_vault, safe_name

router = APIRouter(prefix="/api")

# Summaries wait while any camera has an incident, so the language model stays free for warnings.
incident_service.active = lambda: {u.brain.incident_key for u in list(camera_manager.units.values())
                                   if u.brain.incident_key}


# ---- vault ------------------------------------------------------------------------------------
@router.get("/vault")
def vault_status():
    return evidence_vault.status()


@router.get("/vault/public-key.pem")
def vault_public_key():
    evidence_vault.status()  # creates the key on first use
    return Response(evidence_vault.public_pem, media_type="application/x-pem-file",
                    headers={"Content-Disposition": 'attachment; filename="guardian-public-key.pem"'})


@router.post("/vault/verify")
def vault_verify_all():
    started = evidence_vault.start_full_check()
    return {"started": started, "checking": True, "last_check": evidence_vault.last_check}


@router.get("/vault/verify")
def vault_check_status():
    return {"checking": evidence_vault.checking, "last_check": evidence_vault.last_check}


@router.get("/recordings/{name}/verify")
def verify_recording(name: str):
    if not safe_name(name) or not name.endswith(".mp4") or name.endswith(".part.mp4"):
        raise HTTPException(404, "Recording not found")
    if not (recording_library.dir / name).is_file() and not evidence_vault.known("clip", name):
        raise HTTPException(404, "Recording not found")
    return evidence_vault.verify("clip", name)


# ---- incidents ----------------------------------------------------------------------------------
@router.get("/incidents")
def list_incidents(limit: int = Query(50, ge=1, le=200), recording: str = ""):
    return {"items": incident_service.recent(limit, recording or None)}


@router.get("/incidents/{incident_id}/report")
def incident_report(incident_id: str):
    report = incident_service.report(incident_id)
    if report is None:
        raise HTTPException(404, "Incident not found")
    return report


@router.get("/incidents/{incident_id}/summary")
def incident_summary(incident_id: str):
    summary = incident_service.summary_of(incident_id)
    if summary is None:
        raise HTTPException(404, "Incident not found")
    return summary


@router.post("/incidents/{incident_id}/summary")
def regenerate_summary(incident_id: str):
    summary = incident_service.summary_of(incident_id, regenerate=True)
    if summary is None:
        raise HTTPException(404, "Incident not found")
    return summary


@router.get("/incidents/{incident_id}/package.zip")
def incident_package(incident_id: str):
    result = incident_service.package(incident_id)
    if result is None:
        raise HTTPException(404, "Incident not found")
    path, name = result
    return FileResponse(path, media_type="application/zip", filename=name,
                        background=BackgroundTask(path.unlink, missing_ok=True))
