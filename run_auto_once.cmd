@echo off
setlocal
cd /d "%~dp0"
python scripts\auto_mlb_runner.py --once
