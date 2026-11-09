@echo off
chcp 65001 >nul
setlocal
title Raccoon Checkin - Password Login
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto nopython

echo.
echo   本工具用「手机号 + 密码」直接向官方换取独立登录令牌，
echo   不需要打开小浣熊客户端，也不会因为客户端切号 / 退出登录而失效。
echo.
set "STORE="
set /p "STORE=是否把密码一并存入云端，让每天自动登录、永不管令牌过期？[Y/N，默认 N]："
if /i "%STORE%"=="Y" (
  python 密码登录.py --store-password %*
) else (
  python 密码登录.py %*
)
echo.
pause
exit /b 0

:nopython
echo.
echo [ERROR] Python not found. Please install Python 3 (check "Add to PATH") and retry.
echo.
pause
exit /b 1
