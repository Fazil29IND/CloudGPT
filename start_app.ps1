# CloudGPT PowerShell Launcher
Set-Location $PSScriptRoot

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "             Starting CloudGPT Server...           " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

$pythonPath = if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "[INFO] Using virtual environment (.venv)" -ForegroundColor Green
    ".venv\Scripts\python.exe"
} else {
    Write-Host "[INFO] Virtual environment not found. Using system Python" -ForegroundColor Yellow
    "python"
}

Write-Host "[INFO] Launching server at http://localhost:5001" -ForegroundColor Green
Start-Process "http://localhost:5001"

& $pythonPath app.py
