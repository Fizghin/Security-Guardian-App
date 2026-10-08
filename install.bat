@echo off
setlocal
cd /d "%~dp0"

echo ==================================================
echo  Guardian - installation
echo ==================================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo         Install Python 3.10 - 3.13 from python.org and tick "Add python.exe to PATH".
    pause & exit /b 1
)

if not exist venv (
    echo [1/4] Creating Python environment...
    python -m venv venv || (echo [ERROR] Could not create venv & pause & exit /b 1)
)
call venv\Scripts\activate.bat

echo [2/4] Installing Python packages (first run downloads ~1 GB, mostly PyTorch)...
python -m pip install --upgrade pip >nul
pip install -r backend\requirements.txt || (echo [ERROR] pip install failed & pause & exit /b 1)

if not exist backend\.env (
    copy backend\.env.template backend\.env >nul
    echo        Created backend\.env from the template.
)

echo [3/4] Building the dashboard...
call npm --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js is not installed. Install the LTS version from nodejs.org and run this again.
    pause & exit /b 1
)
pushd frontend
call npm ci || (popd & echo [ERROR] npm install failed & pause & exit /b 1)
call npm run build || (popd & echo [ERROR] dashboard build failed & pause & exit /b 1)
popd

echo [4/4] Local language model...
set "OLLAMA=ollama"
where ollama >nul 2>&1 || set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
"%OLLAMA%" --version >nul 2>&1
if errorlevel 1 (
    echo        Ollama is not installed. Install it from https://ollama.com and run:
    echo            ollama pull llama3.2:3b
    echo        Guardian works without it, but speaks pre-written warnings instead.
) else (
    "%OLLAMA%" list | findstr /r /c:":" >nul 2>&1
    if errorlevel 1 (
        echo        Downloading llama3.2:3b ^(about 2 GB^)...
        "%OLLAMA%" pull llama3.2:3b
    ) else (
        echo        Ollama already has a model installed.
    )
)

echo.
echo ==================================================
echo  Done. Start Guardian with start.bat
echo ==================================================
pause
