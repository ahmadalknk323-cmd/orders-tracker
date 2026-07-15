@echo off
title Orders Tracker - Public URL
echo.
echo =============================================
echo   Share this URL with anyone, anywhere:
echo =============================================
echo.
D:\OpenCode\webapp\cloudflared.exe tunnel --url http://localhost:8081
