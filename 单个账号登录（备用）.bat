@echo off
chcp 65001 >nul
setlocal
title 小浣熊签到 - 单个账号登录（备用，日常不用）
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto nopython
echo ============================================
echo  备用入口：手动输入一个账号登录
echo  日常请直接双击「一键更新.bat」，它会读
echo  AI路由魔改版 里的小浣熊账号，自动登录全部。
echo ============================================
echo.
python "%~dp0password_login.py" %*
echo.
pause
exit /b 0

:nopython
echo.
echo [错误] 没找到 Python。请先安装 Python 3（安装时勾选 Add to PATH）。
echo.
pause
exit /b 1