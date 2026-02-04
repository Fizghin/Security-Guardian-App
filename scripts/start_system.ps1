$ScriptDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $ScriptDir
$BackendPath = Join-Path $ProjectRoot "backend"
$FrontendPath = Join-Path $ProjectRoot "frontend"

Write-Host "Starting AI Security Guardian System..." -ForegroundColor Cyan

# Resolve Python Path
$VenvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonPath = $VenvPython
    Write-Host "Using Python 3.11 from venv: $PythonPath" -ForegroundColor Gray
}
else {
    $PythonPath = "python"
    Write-Host "Using system Python (Warning: might be incompatible)..." -ForegroundColor Yellow
}

# Start Backend
Write-Host "Launching Backend API..."
Start-Process -FilePath $PythonPath -ArgumentList "-m uvicorn main:app --host 0.0.0.0 --port 8000 --reload" -WorkingDirectory $BackendPath -NoNewWindow

# Start Frontend
Write-Host "Launching Frontend Dashboard..."
# On Windows, npm is a batch file (npm.cmd), Start-Process needs the full extension
$NpmCmd = if ($IsWindows) { "npm.cmd" } else { "npm" }
Start-Process -FilePath $NpmCmd -ArgumentList "run dev" -WorkingDirectory $FrontendPath -NoNewWindow

Write-Host "System Services Started." -ForegroundColor Green
Write-Host "Backend: http://localhost:8000"
Write-Host "Frontend: http://localhost:2500"
