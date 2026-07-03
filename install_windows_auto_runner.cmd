@echo off
setlocal
cd /d "%~dp0"
set "TASK_NAME_1350=MLB_Betting_Auto_Update_1350"
set "TASK_NAME_2100=MLB_Betting_Auto_Update_2100"
set "TASK_NAME_0000=MLB_Betting_Auto_Update_0000"
call "%~dp0make_scheduler_launcher.cmd"
set "TASK_CMD=C:\tmp\mlb_betting_auto_update.cmd"

schtasks.exe /Create /TN "%TASK_NAME_1350%" /SC DAILY /ST 13:50 /TR "%TASK_CMD%" /F
if errorlevel 1 (
  echo Failed to create 13:50 scheduled task.
  pause
  exit /b 1
)

schtasks.exe /Create /TN "%TASK_NAME_2100%" /SC DAILY /ST 21:00 /TR "%TASK_CMD%" /F
if errorlevel 1 (
  echo Failed to create 21:00 scheduled task.
  pause
  exit /b 1
)

schtasks.exe /Create /TN "%TASK_NAME_0000%" /SC DAILY /ST 00:00 /TR "%TASK_CMD%" /F
if errorlevel 1 (
  echo Failed to create 00:00 scheduled task.
  pause
  exit /b 1
)

echo.
echo Created Windows scheduled tasks:
echo - %TASK_NAME_1350% daily at 13:50 Taiwan local time, before 14:00 settlement check
echo - %TASK_NAME_2100% daily at 21:00 Taiwan local time
echo - %TASK_NAME_0000% daily at 00:00 Taiwan local time
echo.
echo Each run executes run_auto_once.cmd, settles recent final scores, and rebuilds predictions, tickets, simulator, and Monte Carlo.
echo Logs: %~dp0logs\auto_runner
pause
