@echo off
title CloudGPT Server Launcher

echo ===================================================
echo              Starting CloudGPT Server...
echo ===================================================
echo.

REM Change to the directory where this batch file is located
cd /d "%~dp0"

REM Check if the virtual environment exists
if exist ".venv\Scripts\python.exe" (
    echo [INFO] Using virtual environment [.venv]
    echo [INFO] Starting CloudGPT server...
    echo.
    
    REM Open CloudGPT in the default browser
    start "" "http://localhost:5001"
    
    REM Run the application using the virtual environment
    .venv\Scripts\python.exe app.py
) else (
    echo [INFO] Virtual environment not found.
    echo [INFO] Using system Python
    echo [INFO] Starting CloudGPT server...
    echo.
    
    REM Open CloudGPT in the default browser
    start "" "http://localhost:5001"
    
    REM Run the application using system Python
    python app.py
)

echo.
echo ===================================================
echo              CloudGPT Server Stopped
echo ===================================================
pause