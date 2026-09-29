@echo off
REM ===========================================================================
REM  INSTALL - creates the desktop icon and Start Menu entry.
REM  Double click this file once. Nothing else to do.
REM ===========================================================================
title Install - KattyVision

cd /d "%~dp0"

net session >nul 2>nul
if not errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL.ps1"
)

echo.
pause
