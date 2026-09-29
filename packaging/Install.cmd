@echo off
REM ===========================================================================
REM  KattyVision - first-time installation.
REM
REM  Double click THIS file once. It checks everything, creates the Desktop and
REM  Start Menu icons, and tells you plainly if anything is missing.
REM  Nothing is installed system-wide, no administrator rights needed, and you
REM  can remove the whole program later by deleting this folder.
REM ===========================================================================
title Installing KattyVision
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1"
if errorlevel 1 (
    echo.
    echo   The installer did not finish. Please send a screenshot of this
    echo   window to whoever gave you KattyVision.
    echo.
    pause
) else (
    timeout /t 3 >nul
)
