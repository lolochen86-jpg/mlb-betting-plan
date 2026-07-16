@echo off
setlocal
cd /d "%~dp0"
set "CODEX_PY=C:\Users\I5-10400F\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PY%" (set "PYTHON_EXE=%CODEX_PY%") else (set "PYTHON_EXE=python")
"%PYTHON_EXE%" scripts\run_daily_workflow.py --date %1 --all-predictions --skip-backtest-refresh
