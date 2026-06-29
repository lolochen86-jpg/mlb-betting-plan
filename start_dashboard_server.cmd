@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\I5-10400F\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" -u -m http.server 8765 --bind 127.0.0.1 --directory docs
