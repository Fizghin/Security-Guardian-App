# Guardian

A home/office CCTV system that runs entirely on your own computer. It watches webcams, IP cameras and **old phones turned into cameras**, recognises the people who belong there, talks to strangers using a **local language model**, escalates to a siren if they stay, records clips and alerts you on your phone (Telegram, ntfy push, Discord, e-mail or a webhook). No cloud services are required.

## What it does

- **Several cameras at once**: webcams, IP/RTSP cameras, phones running an IP-camera app, and any phone with a browser (scan a QR code, no app). Each camera has its own incident, recording and speaker.
- **Phones as CCTV**: an old phone streams its camera to Guardian and plays the warnings and siren from its own speaker, right where the intruder is. Battery level and offline alerts included.
- **Two-way audio**: hold a button to talk through a camera's speaker, listen to what a phone camera hears, and get alerts for loud sounds such as breaking glass or a slammed door.
- **Person detection** with YOLOv8 (runs on the CPU; uses an NVIDIA GPU automatically if PyTorch has CUDA).
- **Insider recognition**: add photos (or take them straight from a camera) of household members or staff; recognised people never trigger alarms and can be greeted by name.
- **Escalation** in four levels with configurable timings: greeting, warning, owner alert, siren.
- **Spoken warnings written by a local LLM** (Ollama, LM Studio, llama.cpp…), shaped by adjustable intimidation, humour and persistence. Replies are checked against what is actually happening, and the first warning of each level is prepared in advance so it plays instantly.
- **Recording** of every incident as H.264 MP4, including the seconds *before* the trigger, with automatic clean-up.
- **Dashboard**: live view, event log with filters, chart and CSV export (detections, escalations, alerts and recognised people keep a picture of the moment, linked to the clip recorded at the time), recording library, insider management, settings and system diagnostics. Arm/disarm, panic button, typed or spoken messages through a camera's speaker and a one-click test intrusion.
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

### Try it in GitHub Codespaces

To try Guardian without installing anything, run it in a codespace and open it from any browser:

1. On the repository page choose **Code → Codespaces → Create codespace** (or open <https://codespaces.new/Fizghin/security-guardian-app>). It uses a 4-core machine; the first start takes about 10 minutes while Python packages, the dashboard, Ollama and `llama3.2:3b` are installed.
2. When it is ready, the terminal shows the Guardian address, a generated password and a **camera link**. Open the address (also under *Ports → Guardian*) and sign in.
3. A codespace has no webcam, so Camera 1 is a browser camera: open the camera link in another tab on your laptop, or on a phone, and tap **Start camera**. Warnings and the siren play from that device.
4. For a phone or another person, make the port public first: *Ports* tab → right-click port 8000 → *Port Visibility → Public*. The dashboard still needs the password and the camera page needs its secret link.

Run `bash .devcontainer/run.sh` in the codespace terminal to see the address, password and camera link again; it also starts Guardian if it isn't running. A codespace stops after 30 idle minutes by default and uses your Codespaces hours while it runs.

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

On CPU-only machines the model and the detector share cores. Guardian limits each to half the cores so the cameras keep updating while a warning is being written.

How replies are kept reliable:

- **Facts only.** Each request lists what is true right now: camera location, number of people, time of day, whether video is recording, whether the owner was actually alerted, whether the siren is sounding.
- **Checked before speaking.** Replies that invent police, guards, dogs or weapons, give the voice a name, claim a recording or alert that did not happen, refuse, or repeat an earlier line are rejected. The model gets one retry with the reason, then a pre-written line is used. Pre-written lines are also tagged with the facts they rely on, so they never claim something false either.
- **Instant first warning.** While nothing is happening Guardian writes one line per level in advance (kept across restarts), so the first warning plays immediately even when the model needs 10 seconds on a slow CPU.

Settings → System shows whether the model is loaded, how many lines are prepared and how many replies were rejected. After a reboot, loading the model from disk can take a minute or two on an older computer; pre-written lines are used until it is ready, and while armed Guardian keeps the model in memory so it is not slow again later.

## Cameras and phones

Settings → Cameras → **Add camera** offers four kinds:

| Kind | Use it for |
|---|---|
| **Phone (no app needed)** | Most phones from the last 8 years: Android 5+ with Chrome, or iPhone with iOS 11+ in Safari. |
| **Phone app or network camera** | Older phones running **IP Webcam** (Android) or **DroidCam**, and RTSP/HTTP IP cameras. Guardian tests the connection and shows a frame before saving. |
| **Webcam on this computer** | Built-in or USB cameras; *Find cameras* lists them. |
| **Video file** | A recording, looped, for trying settings out. |

### Turning an old phone into a camera

1. Put the phone on the same Wi-Fi as the computer running Guardian and plug it in.
2. In Settings → Cameras choose **Add camera → Phone**, give it a name (e.g. "Front door") and pick where warnings and your voice play: the phone, this computer, or both.
3. Scan the QR code with the phone. Phone browsers only allow camera access over HTTPS, so Guardian serves this page with its own certificate and the phone shows a one-time warning. Choose *Advanced → Proceed* (Android) or *Show Details → visit this website* (iPhone).
4. Tap **Start camera**. The pairing window shows *Phone connected* within a second or two.

On the phone page you can switch between front and back camera, turn on the flashlight, darken the screen, or pause. Guardian shows the phone's battery and warns you if it goes offline while armed (unplugged, covered, flat battery or lost Wi-Fi; Settings → Escalation). If the phone's browser is too old to open the camera, use the IP-camera-app route instead.

The phone connects to port **8443** (`PHONE_PORT` in `backend/.env`). That port only serves the phone page, the phone's frames and its audio, each phone needs its own secret pairing link, and **Create new link** in the pairing window revokes an old one. Allow the port through the computer's firewall if phones cannot connect (Windows asks the first time).

### Talk, listen and loud sounds

After **Start camera** the phone page also asks for the microphone, separately from the camera: if it is refused, the camera keeps working and the page shows *Microphone off* with a **Try again** button.

- **Hold to talk** (Live page → Controls): hold the button with the mouse or a finger, or press and hold Space while it has focus, and speak. Your voice goes where the camera's warnings go (*Play warnings, the siren and your voice on* in the camera's settings). A phone plays it as you speak and shows *Owner is speaking*. This computer plays it as you speak through `paplay`, `aplay` or `ffplay`; without one of those (macOS without FFmpeg, Windows) it plays what you said when you let go. The text under the button says which applies. One person talks through a camera at a time, and that camera's spoken warnings wait until they finish. Each talk is logged, e.g. *Spoke through Porch for 8 s*.
- **Listen** (phone cameras): plays what the phone's microphone hears, with a level meter. The phone only sends sound while someone listens, and its page says *the owner is listening* meanwhile.
- **Loud sounds** (Settings → Detection): the phone reports how loud it is about four times a second. A sound counts as loud when it is well above what that place usually sounds like (the quiet end of the last minute) and above an absolute floor; *Sound sensitivity* sets how far above (30 dB at 5, 10 dB at 10). Guardian's own warnings, the siren and your voice don't count. *Log them with a picture* (the default) adds an event such as *Loud sound (-8 dB, usually -45 dB)* with the camera's picture, at most every 30 seconds per camera. *Log them and alert me* also records a clip and sends the alert with the picture while armed (at most every 5 minutes per camera). Nothing is spoken, since a sound alone doesn't say who is there.

Browsers only allow the microphone on a secure page, so to talk, open the dashboard at `http://localhost:<port>` on the Guardian computer, or over https (a reverse proxy, a tunnel or a codespace). The camera tile shows a small microphone and level while a phone's microphone is live.

## How alarms escalate

| Level | Default trigger | What happens (defaults) |
|---|---|---|
| 1 Person detected | unrecognised person appears | voice greeting, asks who they are |
| 2 Loitering | still there after 5 s | firmer warning, recording starts |
| 3 Intruder | after 10 s | owner alerted (snapshot), final warning |
| 4 Alarm | after 15 s | siren for up to 60 s |

Only time the person is actually on camera counts. The incident ends 10 s after they leave, the clip is finished 8 s later and, if the incident reached the alert level, the clip is sent to you. The **Panic** button jumps straight to level 4 and stays there until **Reset alarm**. **Disarm** stops all responses while still showing detections.

Timings, which levels record, alert and sound the siren, and the voice's personality are all set in Settings.

### Detection zones

Settings → Cameras → **Zones** lets you outline the areas of a camera's picture that matter, for example your garden or driveway but not the pavement behind it. Only people standing inside a zone count (the point where their feet are, so someone walking behind a wall doesn't count just because their head shows above it). Click to place corners, click the first corner to finish, and drag corners to adjust; corners near the edge of the picture snap onto it. Escape cancels the zone you are drawing, and closing the editor with unsaved changes asks first. A zone's outline can't cross or touch itself, and a zone must cover at least 0.1% of the picture. Zones show as thin outlines on the live picture. With no zones the whole picture counts.

Zones are stored relative to the picture, so they only fit a picture of the same shape. If a camera's picture changes shape after the zones were drawn, for example a phone camera turned on its side, the camera list and the zone editor warn you and the event log notes it once: redraw the zones.

### Arming schedule

Settings → **Schedule** arms Guardian when each period you set starts and disarms it when the period ends, for example every night 22:00–07:00, or on weekdays while you are at work. A period that ends earlier than it starts runs past midnight; periods that touch or overlap disarm only when the last one ends. Times are in the Guardian computer's time zone (Settings shows its offset from UTC) and follow its clock when daylight saving time starts or ends.

The schedule acts once at each start and end. Arming or disarming by hand lasts until the next start or end, also when Guardian restarts; a start or end that passed while Guardian was off is applied when it starts again. Turning the schedule on applies it at once, and editing it changes the armed state only when the edit changes what the schedule says for now. A scheduled disarm never switches off an alarm: during a panic, or while a camera's alarm has reached the level that alerts you, it waits until the alarm is reset or clears. The header shows when the schedule next arms or disarms, and says so when you armed or disarmed by hand.

## Insiders

On the Insiders page, either upload clear photos or use **Add from a camera**: stand in front of a camera, take a snapshot and pick your face. Photos from the camera that will see you work best. Photos with no clear face, several similar-sized faces, a strongly turned head or heavy blur are rejected with the reason, and a warning appears if the face looks like another insider. **Check a photo** shows who Guardian thinks is in any picture and how close the match is.

How recognition decides (OpenCV YuNet + SFace, about 40 MB, downloaded on first use into `backend/storage/models`):

- People are tracked across frames and identified from several looks, not one frame. A clear match identifies someone at once; weaker matches need two looks.
- A newly seen person is *being identified* for up to 2 seconds (Settings → Detection) before counting as a stranger, so a resident walking up is recognised before the system speaks. A clearly visible face that matches nobody is flagged straight away.
- Small faces of people further away get a second, enlarged look. Blurry, tiny or turned faces only count as weak evidence, and a match that is nearly as close to another insider is treated as unknown rather than guessed.
- When people stand close together, each face counts only for the one person whose head it fits best, so an insider's face never vouches for a stranger next to them who has turned away.
- A recognised person who turns away keeps their identity while they stay in view.

Turn on **Greet recognised people by name** (Settings → Voice) to have Guardian say "Welcome back, Sam" at most once per hour per person, only while armed.

## Alerts

Settings → **Notifications** sends alerts with a picture when someone reaches the alert level (level 3 by default), followed by the clip once it is saved. Use any combination:

| Channel | Setup |
|---|---|
| **Telegram** | Create a bot with @BotFather, paste its token, send the bot a message, then press **Find chat** and check that the name it shows is yours (anyone can message a bot). Alerts arrive as photos. |
| **ntfy** | Install the ntfy app, subscribe to a long, hard-to-guess topic (**Suggest** makes one) and paste its address. Free push notifications without an account. A self-hosted ntfy server works too, but sends pictures only with `attachment-cache-dir` set in its `server.yml`; without it, alerts arrive as text and the event log says the picture was left out. |
| **Discord** | Paste a channel webhook URL (it starts with `https://`). |
| **E-mail** | Any SMTP account. For Gmail use an app password. |
| **Webhook** | Guardian POSTs JSON (`title`, `message`, `severity`, `time`, `snapshot_jpeg_base64`) for Home Assistant, Node-RED, n8n and the like. |

**Send test** sends a small test picture through every channel that is on, the same way real alerts go out, and shows the result for each one. On and Off show what is saved; a channel with edits says *Unsaved* until you press Save, and **Remove** clears a saved token or address when you save.

Tokens, passwords and webhook addresses are stored in `backend/storage/settings.json` and never sent back to the dashboard. They can also be preset in `backend/.env` (see `.env.template`); an address there that isn't a valid web address is ignored with a warning when Guardian starts. Error messages from the receiving servers are shown without your tokens and addresses, and HTML error pages are left out.

## Where data lives

Everything Guardian writes is in `backend/storage/`: `guardian.db` (event log), `recordings/`, `faces/` (insider photos), `snapshots/` (event pictures, deleted after the same number of days as recordings; checked every hour), `models/`, `settings.json` (changes made in the dashboard, which take priority over `.env`) and `schedule_state.json` (the schedule's last start or end that Guardian acted on). Delete the folder to start fresh. Data from earlier versions (`sql_app.db`, `faces_db/`) is migrated automatically on first start.

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

- **"No camera signal"**: another app may be using the camera, or the index is wrong. Use *Find cameras*.
- **Phone can't open the page**: check it is on the same Wi-Fi, that the address in the pairing window is this computer's, and that the firewall allows port 8443. A *Not private* warning is expected the first time.
- **Phone page opens but the camera is blocked**: allow camera access for the page in the browser's site settings. Very old browsers can't use the camera at all; use the IP Webcam app route.
- **Phone stops sending when the screen turns off**: keep the page open, use *Dark screen*, and set the screen timeout to the longest option.
- **Warnings say "pre-written line, model unavailable"**: Ollama isn't running or has no model. Check Settings → Language model; the error explains what is missing.
- **No sound**: Settings → Voice shows the speech engine. On Linux install `espeak-ng`; the siren and talking through this computer need `paplay`, `aplay` or `ffplay` (Settings → System shows which is used).
- **Hold to talk says the microphone needs a secure page**: the dashboard was opened over plain http from another device. Use `http://localhost:<port>` on the Guardian computer, or https. If it says the browser refused the microphone, allow it with the icon next to the address.
- **Listen is greyed out**: the phone's microphone is off. Tap **Try again** on the phone, or allow the microphone for the page in the phone browser's site settings.
- **Clip won't play in the browser**: some Chromium builds lack H.264. Use Download, or Chrome/Edge/Firefox/Safari.
- **Port 8000 is used by another program**: Guardian moves to the next free port and prints the address; set `PORT=` in `backend/.env` to choose one. Starting Guardian while it already runs just prints its address.
- **macOS: "not authorized to capture video" or no webcam picture**: the first time Guardian starts with a webcam, macOS asks whether your terminal app may use the camera. Click OK. If you declined, turn the terminal app (Terminal, iTerm, …) on in System Settings → Privacy & Security → Camera and restart Guardian. Phone cameras work either way.

## Security

By default the dashboard listens on `127.0.0.1` (this computer only) and has no password. If other devices can reach it (`HOST=0.0.0.0`, a reverse proxy or a tunnel), set `DASHBOARD_PASSWORD=` in `backend/.env`: browsers then get a sign-in page (scripts can use HTTP Basic auth with any user name). Behind an https reverse proxy or tunnel, also set `PUBLIC_URL=https://your-address` so phone pairing links use that address and phones can stream from outside your Wi-Fi.

The phone port (8443) is reachable from your network but serves only the phone camera page. Every other path, including the dashboard, the API and the live video, returns 404 there, and sending video or audio requires a phone's secret pairing link. Set `PHONE_PORT=0` if you don't use phone cameras.

Talk and listen accept connections only from the dashboard's own pages, so other websites open in your browser can't use a camera's microphone or speaker. Anyone who can open the dashboard can, which is another reason to set `DASHBOARD_PASSWORD` when other devices can reach it.
