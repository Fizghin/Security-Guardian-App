@echo off
setlocal

echo ===================================================
echo AI Security Guardian - Installation Script
echo ===================================================

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b 1
)

REM Create Virtual Environment
if not exist "venv" (
    echo [INFO] Creating Python virtual environment...
    python -m venv venv
) else (
    echo [INFO] Virtual environment already exists.
)

REM Activate Virtual Environment
call venv\Scripts\activate

REM Upgrade PIP
echo [INFO] Upgrading pip...
python -m pip install --upgrade pip

REM Install Backend Requirements
echo [INFO] Installing backend dependencies...
pip install -r backend/requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install backend dependencies.
    pause
    exit /b 1
)

REM Check Node.js
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo Please install Node.js (LTS) and try again.
    pause
    exit /b 1
)

REM Install Frontend Dependencies
echo [INFO] Installing frontend dependencies...
cd frontend
call npm install
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install frontend dependencies.
    cd ..
    pause
    exit /b 1
)
cd ..

echo ===================================================
echo Installation Complete!
echo You can now run the application using start.bat
echo ===================================================
pause
