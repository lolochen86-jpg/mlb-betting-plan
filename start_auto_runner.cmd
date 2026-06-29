@echo off
setlocal
cd /d "%~dp0"
python scripts\auto_mlb_runner.py --interval-minutes 60 --start-now --open-dashboard --publish
pause
