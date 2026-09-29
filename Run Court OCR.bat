@echo off
REM ===========================================================================
REM  This launcher was renamed to "KattyVision.bat".
REM
REM  It is kept here, and kept working, so that a shortcut or a note on a
REM  staff PC that still points at the old name does not simply fail.
REM ===========================================================================
title KattyVision
cd /d "%~dp0"

if exist "KattyVision.bat" (
    call "KattyVision.bat"
) else (
    echo.
    echo   "KattyVision.bat" is missing from this folder.
    echo   Please use the KattyVision icon instead.
    echo.
    pause
)
