@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo AI Security Guardian - Startup Script
echo ===================================================

REM Check Virtual Environment
if not exist "venv" (
    echo [ERROR] Virtual environment not found. 
    echo Please run install.bat first.
    pause
    exit /b 1
)

REM Activate Virtual Environment
call venv\Scripts\activate

echo [INFO] Starting Backend Server...
start "AI Guardian Backend" cmd /k "cd backend && python -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload"

echo [INFO] Starting Frontend Interface...
start "AI Guardian Frontend" cmd /k "cd frontend && npm run dev -- --port 2500"

echo.
echo ===================================================
echo System is starting up...
echo Backend: http://127.0.0.1:8002/docs
echo Frontend: http://127.0.0.1:2500
echo ===================================================
echo Press any key to close this launcher (servers will keep running)...
pause >nul
