@echo off
cd /d "%~dp0\.."

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "scripts\tray_app.py"
    exit /b 0
)

pythonw --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    start "" pythonw "scripts\tray_app.py"
    exit /b 0
)

start "" python "scripts\tray_app.py"
exit /b 0
