import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import cv2
import json
import time
import os
from dotenv import load_dotenv

load_dotenv()
import json
import time

from services.video_service import video_service
from services.detection_service import detection_service
from services.brain_service import brain_service
from models_fusion import engine, Base, get_db, SessionLocal, SecurityEvent, Detection
from sqlalchemy.orm import Session
from fastapi import Depends

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("System Startup: Initializing AI Security Guardian...")
    # Ensure tables exist (alternative to init_db script)
    Base.metadata.create_all(bind=engine)
    video_service.start()
    yield
    # Shutdown
    print("System Shutdown: releasing resources...")
    # Delay import or use service to avoid circular if any
    from services.recording_service import recording_service
    recording_service.stop_all()
    video_service.stop()

app = FastAPI(
    title="AI Security Guardian API",
    description="Backend for AI-powered Security System",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "AI Security Guardian System Online", "status": "active"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy", 
        "video_source": video_service.source,
        "is_running": video_service.is_running
    }

from services.stream_service import stream_manager

@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        await stream_manager.stream(websocket)
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")

@app.post("/api/panic")
async def trigger_panic():
    print("PANIC BUTTON TRIGGERED!")
    brain_service.threat_level = 4
    brain_service._execute_countermeasures(time.time())
    return {"status": "critical_alert_sent"}

@app.post("/api/simulate/person")
async def trigger_simulation():
    print("SIMULATION TRIGGERED: Injecting Fake Person for 30s")
    stream_manager.trigger_simulation(30.0)
    return {"status": "simulation_started"}

@app.post("/api/config")
async def update_config(config: dict):
    from services.ai_service import ai_service
    ai_service.update_config(config)
    return {"status": "config_updated", "current_personality": ai_service.personality}

@app.get("/api/events")
async def get_events(limit: int = 50, db: Session = Depends(get_db)):
    events = db.query(SecurityEvent).order_by(SecurityEvent.timestamp.desc()).limit(limit).all()
    return events

@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    # Calculate active threats (just current threat level)
    threat_level = brain_service.threat_level
    
    # Count events today (approximation)
    event_count = db.query(SecurityEvent).count()
    
    return {
        "active_threats": 1 if threat_level > 0 else 0,
        "threat_level": threat_level,
        "system_status": "ONLINE" if video_service.is_running else "OFFLINE",
        "total_events": event_count,
        "ai_personality": brain_service.ai_service.personality,
        "last_ai_message": brain_service.last_spoken_text,
        "message_time": brain_service.last_spoken_time
    }


@app.post("/api/faces/upload")
async def upload_face(name: str = Form(...), file: UploadFile = File(...)):
    """
    Upload a face image for a known person (Insider).
    """
    try:
        # Sanitize name (simple alphanumeric check + spaces)
        safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '_', '-')]).strip()
        if not safe_name:
             return {"status": "error", "message": "Invalid name provided."}

        # Create directory
        db_path = os.path.join(os.getcwd(), "backend", "faces_db", safe_name)
        os.makedirs(db_path, exist_ok=True)
        
        # Save file
        file_location = os.path.join(db_path, file.filename)
        with open(file_location, "wb+") as file_object:
            file_object.write(await file.read())
            
        print(f"Face uploaded for {safe_name}: {file_location}")
        return {"status": "success", "message": f"Face for {safe_name} added successfully."}
    except Exception as e:
        print(f"Upload failed: {e}")
        return {"status": "error", "message": str(e)}
