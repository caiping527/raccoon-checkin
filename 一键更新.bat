@echo off
chcp 65001 >nul
setlocal
title 小浣熊签到 - 一键更新（登录全部账号并同步到 GitHub）
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto nopython
echo ============================================
echo  小浣熊签到 · 一键更新
echo  读取 AI路由魔改版 里的全部小浣熊账号，逐个登录，
echo  再依次把令牌写进 GitHub 并触发一次云端自检。
echo  只预览不写入：  "%~nx0" --dry-run
echo ============================================
echo.
python "%~dp0batch_login.py" %*
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%

:nopython
echo.
echo [错误] 没找到 Python。请先安装 Python 3.9 及以上版本（安装时勾选 Add to PATH）。
echo.
pause
exit /b 1