@echo off
chcp 65001 >nul
setlocal
title Raccoon Checkin - Password Login
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto nopython
python password_login.py %*
echo.
pause
exit /b 0

:nopython
echo.
echo [ERROR] Python not found. Please install Python 3 (check "Add to PATH") and retry.
echo.
pause
exit /b 1
