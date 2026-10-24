@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo   GConnect - Remote GPU Development Platform Setup
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
    echo   - Docker CLI: Not found - terminal will show a notice
)

nvidia-smi >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   - NVIDIA GPU: Detected
) else (
    echo   - NVIDIA GPU: Driver not found - using simulated telemetry
)

REM Honor the same env vars as the daemon (see .env.example).
if "%CONNECT_ALLOW_LAN%"=="true" (
    if "%CONNECT_HOST%"=="" set CONNECT_HOST=0.0.0.0
)
if "%CONNECT_HOST%"=="" set CONNECT_HOST=127.0.0.1
if "%CONNECT_PORT%"=="" set CONNECT_PORT=8000

echo.
echo =======================================================
echo   Starting GConnect Host Daemon...
echo   Web IDE:             http://%CONNECT_HOST%:%CONNECT_PORT%
echo   Inference Stream:    Port %CONNECT_INFERENCE_PORT% (Forwarded to container)
echo =======================================================
echo.

python -m uvicorn backend.main:app --host %CONNECT_HOST% --port %CONNECT_PORT% --reload

pause
