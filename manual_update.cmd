@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

echo.
echo ========================================
echo MLB 投注計畫 - 手動更新
echo ========================================
echo.
echo 直接按 Enter：更新今天
echo 輸入日期：更新指定 MLB 日期，例如 2026-07-14
echo.
set /p TARGET_DATE=請輸入日期：

if "%TARGET_DATE%"=="" (
  echo.
  echo 開始更新今天資料...
  python scripts\auto_mlb_runner.py --once --publish --open-dashboard
) else (
  echo.
  echo 開始更新 %TARGET_DATE% 資料...
  python scripts\auto_mlb_runner.py --once --publish --open-dashboard --date %TARGET_DATE%
)

echo.
if errorlevel 1 (
  echo 手動更新失敗，請查看上方錯誤訊息或 logs\auto_runner。
) else (
  echo 手動更新完成，網頁已重建並嘗試上傳 GitHub。
)
echo.
pause
