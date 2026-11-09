@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小浣熊 - 账号切换（免登出）
python 账号切换.py
echo.
pause
