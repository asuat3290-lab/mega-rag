@echo off
setlocal
cd /d "%~dp0"
py -3.13 portable_run.py ui
set "result=%errorlevel%"
pause
exit /b %result%
