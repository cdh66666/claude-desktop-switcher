@echo off
chcp 65001 >nul
rem 一键打包成单 exe（产物在 dist\ClaudeDesktopSwitcher.exe）
cd /d "%~dp0\.."
echo 检查 PyInstaller...
pip show pyinstaller >nul 2>&1 || pip install pyinstaller
echo 开始打包，约 30 秒...
pyinstaller ClaudeDesktopSwitcher.spec --clean --noconfirm
echo.
echo 打包完成：dist\ClaudeDesktopSwitcher.exe
pause
