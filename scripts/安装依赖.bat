@echo off
chcp 65001 > nul
cd /d "%~dp0\.."
echo Installing dependencies...
pip install -r requirements.txt
echo.
echo Done. You can now run scripts\启动.bat or use the Start Menu shortcut.
pause
