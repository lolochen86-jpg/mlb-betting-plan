@echo off
setlocal
cd /d "%~dp0"
set "TASK_NAME=MLB_Betting_Auto_Update"
call "%~dp0make_scheduler_launcher.cmd"
set "TASK_CMD=C:\tmp\mlb_betting_auto_update.cmd"
schtasks /Create /TN "%TASK_NAME%" /SC HOURLY /MO 1 /TR "%TASK_CMD%" /ST 08:00 /F
echo.
echo 已建立 Windows 工作排程：%TASK_NAME%
echo 之後 Windows 會每小時自動執行一次今日 MLB 更新。
echo Log 位置：%~dp0logs\auto_runner
pause
