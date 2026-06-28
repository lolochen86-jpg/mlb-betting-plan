@echo off
setlocal
set "TASK_NAME_2100=MLB_Betting_Auto_Update_2100"
set "TASK_NAME_0000=MLB_Betting_Auto_Update_0000"

schtasks.exe /Delete /TN "%TASK_NAME_2100%" /F >nul 2>nul
schtasks.exe /Delete /TN "%TASK_NAME_0000%" /F >nul 2>nul
schtasks.exe /Delete /TN "MLB_Betting_Auto_Update" /F >nul 2>nul

echo.
echo Removed Windows scheduled tasks:
echo - %TASK_NAME_2100%
echo - %TASK_NAME_0000%
echo Legacy hourly task was also removed if it existed.
pause
