@echo off
setlocal
cd /d "%~dp0"
set "CODEX_PY=C:\Users\I5-10400F\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PY%" (set "PYTHON_EXE=%CODEX_PY%") else (set "PYTHON_EXE=python")
"%PYTHON_EXE%" scripts\auto_mlb_runner.py --interval-minutes 60 --start-now --open-dashboard --publish
pause
