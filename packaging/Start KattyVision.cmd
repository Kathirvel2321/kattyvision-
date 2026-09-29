@echo off
REM ===========================================================================
REM  KattyVision - start the program.
REM
REM  You will not normally need this file: after installing there is a Desktop
REM  icon called "KattyVision".  It is here in case the icon is deleted, and so
REM  that there is one obvious way to start the program.
REM ===========================================================================
title KattyVision
cd /d "%~dp0"

REM Use ONLY the Python inside this folder, and ignore any Python the user has
REM installed for their own work.  Without PYTHONNOUSERSITE the program could
REM pick up packages out of the user's profile and behave differently on a
REM different PC.
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="

if not exist "python\pythonw.exe" (
    echo.
    echo   This folder looks incomplete - the "python" folder is missing.
    echo   Please copy the whole KattyVision folder again.
    echo.
    pause
    exit /b 1
)

start "" "python\pythonw.exe" -m court_ocr.app
exit /b 0
