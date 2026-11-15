@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_recognition.ps1"
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
