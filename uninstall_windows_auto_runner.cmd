@echo off
setlocal
set "TASK_NAME=MLB_Betting_Auto_Update"
schtasks /Delete /TN "%TASK_NAME%" /F
echo.
echo 已移除 Windows 工作排程：%TASK_NAME%
pause
