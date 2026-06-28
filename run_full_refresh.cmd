@echo off
setlocal
cd /d "%~dp0"
python scripts\run_daily_workflow.py --date %1 --all-predictions
