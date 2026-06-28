@echo off
setlocal
cd /d "%~dp0"

echo [1/4] 更新今日 MLB 資料與本機網頁...
call "%~dp0立刻更新一次.cmd"
if errorlevel 1 (
  echo 更新失敗，已停止上傳。
  pause
  exit /b 1
)

echo.
echo [2/4] 檢查 Git 變更...
git add .
git diff --cached --quiet
if not errorlevel 1 (
  echo 沒有新的變更需要上傳。
  pause
  exit /b 0
)

for /f "tokens=1-4 delims=/ " %%a in ("%date%") do set TODAY=%%a-%%b-%%c
for /f "tokens=1-2 delims=:." %%a in ("%time%") do set NOW=%%a%%b

echo.
echo [3/4] 建立提交...
git commit -m "Update MLB betting site %TODAY% %NOW%"
if errorlevel 1 (
  echo Git commit 失敗，已停止上傳。
  pause
  exit /b 1
)

echo.
echo [4/4] 推送到 GitHub...
git push
if errorlevel 1 (
  echo Git push 失敗，請確認網路或 GitHub 登入狀態。
  pause
  exit /b 1
)

echo.
echo 完成。GitHub Pages 會自動部署最新網頁。
pause
