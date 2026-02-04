@echo off
echo ===================================================
echo     STARTING AI SECURITY GUARDIAN SYSTEM
echo ===================================================

echo [1/3] Launching Backend Server...
start "Guardian Backend" cmd /k "cd backend && uvicorn main:app --reload"

echo [2/3] Launching Frontend Interface...
start "Guardian Frontend" cmd /k "cd frontend && npm run dev"

echo [3/3] Opening Dashboard in Browser...
timeout /t 5 >nul
start http://localhost:2500

echo ===================================================
echo     System Started! Don't close this window.
echo ===================================================
pause
