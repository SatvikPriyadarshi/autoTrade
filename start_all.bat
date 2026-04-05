@echo off
setlocal

REM Always run from this script's directory
cd /d "%~dp0"

echo ============================================
echo   Satvik bt-1 (SMC) - Startup Launcher
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python is not installed or not in PATH.
  echo Install Python, then run this file again.
  pause
  exit /b 1
)

echo Starting dashboard server...
start "Satvik bt-1 Dashboard" cmd /k "cd /d "%~dp0" && python server.py"

echo Starting trading bot (v2.0 — enhanced)...
start "Satvik bt-1 Bot" cmd /k "cd /d "%~dp0" && python main.py"

echo.
echo Launched both processes in separate windows.
echo - Dashboard: http://localhost:5000
echo - Bot logs: see "Satvik bt-1 Bot" window
echo.
echo If the bot exits with MT5 authorization error, fix MT5 login/server in terminal.
pause
