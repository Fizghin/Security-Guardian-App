# Guardian

A home/office security camera that runs entirely on your own computer. It watches a webcam or IP camera, recognises the people who belong there, talks to strangers through your speakers using a **local language model**, escalates to a siren if they stay, records clips and can alert you on Discord or by e-mail. No cloud services are required.

## What it does

- **Person detection** with YOLOv8 (runs on the CPU; uses an NVIDIA GPU automatically if PyTorch has CUDA).
- **Insider recognition**: add photos of household members or staff; recognised people never trigger alarms.
- **Escalation** in four levels with configurable timings: greeting, warning, owner alert, siren.
- **Spoken warnings written by a local LLM** (Ollama, LM Studio, llama.cpp…), shaped by adjustable intimidation, humour and persistence. If the model is unavailable, pre-written lines are used so the system never goes silent.
- **Recording** of every incident as H.264 MP4, including the seconds *before* the trigger, with automatic clean-up.
- **Dashboard**: live view, event log with filters, chart and CSV export, recording library, insider management, settings and system diagnostics. Arm/disarm, panic button, talk-through-speaker and a one-click test intrusion.
- Monitoring runs in the background whether or not the dashboard is open.

## Requirements

| | |
|---|---|
| OS | Windows 10/11, macOS, or Linux |
| Python | 3.10 – 3.13 |
| Node.js | LTS (only to build the dashboard once) |
| Ollama | optional but recommended: <https://ollama.com> |
| Camera | any webcam, a phone running DroidCam, or an RTSP/HTTP IP camera |

FFmpeg is bundled through `imageio-ffmpeg`; a system FFmpeg is used if present.

## Install and run

**Windows**

1. Install Python (tick *Add python.exe to PATH*), Node.js LTS and, ideally, Ollama.
2. Double-click `install.bat`. It creates `venv`, installs packages, builds the dashboard and downloads the `llama3.2:3b` model if Ollama is installed.
3. Double-click `start.bat`. The dashboard opens at <http://localhost:8000>.

**Linux / macOS**

```bash
./install.sh
./start.sh
```

On Linux without an NVIDIA GPU, `PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu ./install.sh` installs the much smaller CPU build of PyTorch. For spoken warnings install `espeak-ng` (`sudo apt install espeak-ng`).

`start` checks that the local model server is running and starts `ollama serve` itself if needed.

## Local language model

Guardian talks to the model over HTTP on your machine; nothing leaves it.

| Server | Settings → Language model |
|---|---|
| Ollama (default) | Server type *Ollama*, URL `http://localhost:11434` |
| LM Studio | *OpenAI-compatible*, URL `http://localhost:1234/v1` |
| llama.cpp server | *OpenAI-compatible*, URL `http://localhost:8080/v1` |

The model list in Settings shows what is actually installed on the server; *Automatic* uses the first one. **Test warning** generates a line at any level so you can hear the tone before relying on it.

Model suggestions for Ollama:

- `llama3.2:3b`: good warnings, about 2 GB. The default.
- `llama3.2:1b`: for older or slower CPUs. Quicker, but less reliable at sticking to the facts.
- Anything larger runs fine with a GPU.

On CPU-only machines the model and the detector share cores. Guardian limits each to half the cores so the camera keeps updating while a warning is being written. Warnings arrive a few seconds later on slow machines, and the time limit in Settings decides when to fall back to a pre-written line.

## Cameras

Settings → Camera → Source accepts:

- `auto`: the first local camera that works
- `0`, `1`, …: a specific local camera (**Find local cameras** lists them)
- `http://PHONE-IP:4747/video`: DroidCam
- `rtsp://user:pass@CAMERA-IP/stream`: most IP cameras
- a video file path (looped), useful for trying things out
- `none`: no camera

If a camera drops out, Guardian keeps retrying the same source.

## How alarms escalate

| Level | Default trigger | What happens (defaults) |
|---|---|---|
| 1 Person detected | unrecognised person appears | voice greeting, asks who they are |
| 2 Loitering | still there after 5 s | firmer warning, recording starts |
| 3 Intruder | after 10 s | owner alerted (snapshot), final warning |
| 4 Alarm | after 15 s | siren for up to 60 s |

Only time the person is actually on camera counts. The incident ends 10 s after they leave, the clip is finished 8 s later and, if the incident reached the alert level, the clip is sent to you. The **Panic** button jumps straight to level 4 and stays there until **Reset alarm**. **Disarm** stops all responses while still showing detections.

Timings, which levels record, alert and sound the siren, and the voice's personality are all set in Settings.

## Insiders

On the Insiders page, add a name and one or more clear, front-facing photos. Photos without a detectable face are rejected. Recognition uses OpenCV's YuNet and SFace models (about 40 MB, downloaded on first use into `backend/storage/models`). A recognised person who turns away is trusted for a short grace period.

## Alerts

Set these in `backend/.env` (copied from `backend/.env.template` by the installer) and restart:

```ini
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password
ALERT_EMAIL=where-to-send@example.com
```

Settings → Notifications → **Send test** checks them.

## Where data lives

Everything Guardian writes is in `backend/storage/`: `guardian.db` (event log), `recordings/`, `faces/` (insider photos), `models/` and `settings.json` (changes made in the dashboard, which take priority over `.env`). Delete the folder to start fresh. Data from earlier versions (`sql_app.db`, `faces_db/`) is migrated automatically on first start.

## Development

```bash
# backend tests
venv/bin/python -m pytest backend          # Windows: venv\Scripts\python -m pytest backend

# dashboard with hot reload on http://localhost:2500 (proxies to the backend on :8000)
venv/bin/python backend/run_server.py --no-browser
cd frontend && npm run dev

# checks
cd frontend && npm run lint && npm run build
```

The API is documented at <http://localhost:8000/docs> while the server runs.

## Troubleshooting

- **"No camera signal"**: another app may be using the camera, or the index is wrong. Use *Find local cameras*.
- **Warnings say "pre-written line, model unavailable"**: Ollama isn't running or has no model. Check Settings → Language model; the error explains what is missing.
- **No sound**: Settings → Voice shows the speech engine. On Linux install `espeak-ng`; the siren needs `paplay`, `aplay` or `ffplay`.
- **Clip won't play in the browser**: some Chromium builds lack H.264. Use Download, or Chrome/Edge/Firefox/Safari.
- **Port in use**: set `PORT=` in `backend/.env`.

## Security

The dashboard has no login. By default it listens on `127.0.0.1` (this computer only). Setting `HOST=0.0.0.0` lets other devices reach it, so only do that on a network you trust.
