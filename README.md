# AI Security Guardian System

A production-ready AI-powered security system with real-time person detection, natural language conversation, and a sci-fi futuristic dashboard.

## Features

- **Video Input**: Supports Webcams and DroidCam (IP Camera).
- **AI Detection**: YOLOv8 Person Detection.
- **Conversation Engine**: OpenAI GPT-4o / Ollama Llama 3 Integration.
- **Sci-Fi Dashboard**: React + TailwindCSS + Framer Motion.
- **Recording**: FFmpeg integration for event capture.

## Prerequisites

- Python 3.10+
- Node.js 16+
- FFmpeg (added to system PATH)
- Webcam or DroidCam Client

## Installation

1.  **Backend Setup**:
    ```powershell
    cd backend
    pip install -r requirements.txt
    python init_db.py
    ```

2.  **Frontend Setup**:
    ```powershell
    cd frontend
    npm install
    # (Optional) npm run build
    ```

3.  **Environment Variables**:
    Create `backend/.env` (optional, for OpenAI key):
    ```
    OPENAI_API_KEY=sk-...
    ```

## Master Debugger Remediation (2026 Update)

This system has undergone a complete **Master Debugger Audit & remediation**, hardening it for production use:
- **Async Neural Pipeline**: Backend migrated to `AsyncOpenAI` for non-blocking concurrent detections.
- **Service Decoupling**: Modular `BrainService`, `StreamManager`, and `AIService` architecture.
- **Sequential Voice Logic**: Thread-safe audio queuing for professional, non-overlapping announcements.
- **Visual Intelligence**: Analytics and Timeline synchronized with live database state.
- **Process Protection**: Safe resource release (FFmpeg, Camera) on system shutdown.

## Usage

1. **Verify Environment**:
   ```powershell
   python backend/validate_env.py
   ```

2. **Run System**:
   Use the root-level startup script:
   ```powershell
   ./start_system.bat
   ```

## Configuration

- **Personality**: AI personality (Intimidation/Humor) can be adjusted dynamically via the Dashboard.
- **Security Protocols**: Threat escalation durations and recording triggers are managed in `backend/services/brain_service.py`.
