@echo off
setlocal
set "LAUNCHER=C:\tmp\mlb_betting_auto_update.cmd"
set "LINKDIR=C:\tmp\mlb_betting_project"
if not exist C:\tmp mkdir C:\tmp
if not exist "%LINKDIR%" mklink /J "%LINKDIR%" "%~dp0"
> "%LAUNCHER%" echo @echo off
>> "%LAUNCHER%" echo cd /d "%LINKDIR%"
>> "%LAUNCHER%" echo call "%LINKDIR%\run_auto_once.cmd"
echo 已建立排程啟動器：%LAUNCHER%
