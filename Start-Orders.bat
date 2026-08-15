@echo off
chcp 65001 >nul
color 0A
title Orders Tracker Server
echo ========================================
echo   Orders Tracker
echo ========================================
echo.
echo   شغّل الخادوم على:
echo   http://127.0.0.1:8081
echo.
echo   لا تقفل هذه النافذة!
echo ========================================
echo.
python "%~dp0app.py"
pause
