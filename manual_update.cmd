@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

set "CODEX_PY=C:\Users\I5-10400F\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PY%" (
  set "PYTHON_EXE=%CODEX_PY%"
) else (
  set "PYTHON_EXE=python"
)

echo.
echo ========================================
echo MLB 投注計畫 - 手動更新
echo ========================================
echo.
echo 直接按 Enter 會更新今天；也可以輸入日期，例如 2026-07-16
echo.
set /p TARGET_DATE=請輸入目標日期：

if "%TARGET_DATE%"=="" (
  echo.
  echo 正在更新今天資料...
  "%PYTHON_EXE%" scripts\auto_mlb_runner.py --once --publish --open-dashboard
) else (
  echo.
  echo 正在更新 %TARGET_DATE% 資料...
  "%PYTHON_EXE%" scripts\auto_mlb_runner.py --once --publish --open-dashboard --date %TARGET_DATE%
)

echo.
if errorlevel 1 (
  echo 手動更新失敗，請查看 logs\auto_runner 裡的日誌。
) else (
  echo 手動更新完成，網頁和 GitHub 發布流程已執行。
)
echo.
pause
