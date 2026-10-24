@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo   Connect - Remote GPU Development Platform Setup
echo =======================================================
echo.

cd /d "%~dp0\.."

python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH. Please install Python 3.10+.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [1/3] Creating virtual environment .venv...
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [2/3] Installing dependencies from backend/requirements.txt...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r backend\requirements.txt

echo.
echo [3/3] Checking environment status:
docker --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   - Docker CLI: Detected
) else (
    echo   - Docker CLI: Not found - running in local shell fallback mode
)

nvidia-smi >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   - NVIDIA GPU: Detected
) else (
    echo   - NVIDIA GPU: Driver not found - using simulated telemetry
)

echo.
echo =======================================================
echo   Starting Connect Host Daemon...
echo   Web IDE:             http://localhost:8000
echo   Inference Stream:    Port 8001 (Forwarded to container)
echo =======================================================
echo.

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

pause

