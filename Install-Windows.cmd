@echo off
setlocal
cd /d "%~dp0"
py -3.13 portable_install.py %*
set "result=%errorlevel%"
if not "%result%"=="0" echo Installation failed. Read the error above; no success is claimed.
pause
exit /b %result%
