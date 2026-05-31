@echo off
chcp 65001 >nul
rem 切换到项目根目录（脚本在 scripts/ 子目录里）
cd /d "%~dp0\.."
echo 正在创建开始菜单和桌面快捷方式...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $dir=$PWD.Path; $py=(Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; if(-not $py){$py=(Get-Command python.exe).Source}; foreach($loc in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){ $l=$ws.CreateShortcut((Join-Path $loc 'Claude 账号切换器.lnk')); $l.TargetPath=$py; $l.Arguments=('\"'+(Join-Path $dir 'claude_switcher.py')+'\"'); $l.WorkingDirectory=$dir; $l.IconLocation=(Join-Path $dir 'assets\claude-icon.ico'); $l.Description='Claude Desktop 多账号切换器'; $l.Save() }"
echo 完成：已在「开始菜单」和「桌面」创建 "Claude 账号切换器" 快捷方式。
pause
