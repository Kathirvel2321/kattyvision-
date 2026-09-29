@echo off
REM ===========================================================================
REM  KattyVision - double click to open the app.
REM  Launches with pythonw so no black console window appears.
REM ===========================================================================
title KattyVision

cd /d "%~dp0"

REM ---- find pythonw (windowless interpreter), fall back to python
set "PYW="
for %%P in (pythonw.exe) do if not defined PYW set "PYW=%%~$PATH:P"
if not defined PYW (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYW if exist "%%~dpIpythonw.exe" set "PYW=%%~dpIpythonw.exe"
    )
)
if not defined PYW set "PYW=python"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Python was not found on this PC.
    echo   Install Python 3.10+ from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" while installing.
    echo.
    pause
    exit /b 1
)

if not exist "court_ocr\app.py" (
    echo.
    echo   This file must stay next to the "court_ocr" folder.
    echo.
    pause
    exit /b 1
)

python -c "import pymupdf" >nul 2>nul
if errorlevel 1 (
    echo   First run: installing the required components, please wait...
    python -m pip install --upgrade pymupdf pillow
    if errorlevel 1 (
        echo   Could not install the components. Please run Install App.bat instead.
        pause
        exit /b 1
    )
)

start "" "%PYW%" -m court_ocr.app
exit /b 0
