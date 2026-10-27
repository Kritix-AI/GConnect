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

REM Host mode defaults: enable LAN exposure so teammates can connect.
if "%CONNECT_ALLOW_LAN%"=="" set CONNECT_ALLOW_LAN=true
if "%CONNECT_HOST%"=="" set CONNECT_HOST=0.0.0.0
if "%CONNECT_PORT%"=="" set CONNECT_PORT=8000
if "%CONNECT_INFERENCE_PORT%"=="" set CONNECT_INFERENCE_PORT=8001

echo.
echo =======================================================
echo   GConnect GPU Host Daemon Active!
python -c "from backend.network_utils import get_host_network_addresses; from backend import auth; info = get_host_network_addresses(); print('  Teammate URL:     http://' + str(info['recommended_ip']) + ':%CONNECT_PORT%'); print('  Access Token:     ' + auth.get_token()); print('  Audio Stream:     Port %CONNECT_INFERENCE_PORT%')"
echo   Local Web IDE:    http://localhost:%CONNECT_PORT%
echo =======================================================
echo.

python -m uvicorn backend.main:app --host %CONNECT_HOST% --port %CONNECT_PORT% --reload

pause
