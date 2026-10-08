@echo off
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo [ERROR] Guardian is not installed yet. Run install.bat first.
    pause & exit /b 1
)
if not exist frontend\dist\index.html (
    echo [ERROR] The dashboard has not been built. Run install.bat again.
    pause & exit /b 1
)

venv\Scripts\python.exe backend\run_server.py %*
pause
