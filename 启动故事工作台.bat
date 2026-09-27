@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto offline
start "词间故事工作台" http://127.0.0.1:8765
py -3 server.py
if errorlevel 1 goto offline
exit /b 0
:offline
echo Python 运行环境不可用，正在打开离线版工作台。
start "" "%~dp0index.html"
pause
