@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo   SMC AI Trading Bot - Stop Launcher
echo ============================================
echo.

echo Stopping bot/dashboard Python processes...
taskkill /F /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq *bot.py*" >nul 2>&1
taskkill /F /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq *server.py*" >nul 2>&1

REM Fallback: stop any python process running these scripts by command line
powershell -NoProfile -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -and ( $_.CommandLine -match 'D:\\trading_bot\\bot.py' -or $_.CommandLine -match 'D:\\trading_bot\\server.py' ) }; foreach($p in $procs){ Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Closing launcher windows...
taskkill /F /FI "WINDOWTITLE eq SMC Dashboard Server*" /IM cmd.exe >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq SMC Trading Bot*" /IM cmd.exe >nul 2>&1

echo.
echo Stop request sent.
echo If anything is still running, close its window manually.
pause
