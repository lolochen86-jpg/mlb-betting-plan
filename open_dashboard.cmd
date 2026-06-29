@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\I5-10400F\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
netstat -ano | findstr /R /C:":8765 .*LISTENING" >nul
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -WindowStyle Minimized -WorkingDirectory '%~dp0' -FilePath 'cmd.exe' -ArgumentList '/k start_dashboard_server.cmd'"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 2"
)
start "" "http://127.0.0.1:8765/index.html"
