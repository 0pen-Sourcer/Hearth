@echo off
REM Hearth CLI launcher - simple direct invocation.
REM Want it in Windows Terminal instead? Use Hearth-cli.bat.

setlocal
title Hearth - local AI for your machine
chcp 65001 >nul

set "HEARTH_HOME=%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%HEARTH_HOME%"

if exist "%HEARTH_HOME%.venv\Scripts\python.exe" (
    "%HEARTH_HOME%.venv\Scripts\python.exe" "%HEARTH_HOME%hearth_cli.py" %*
) else (
    python "%HEARTH_HOME%hearth_cli.py" %*
)

REM Pause on error so a crash doesn't disappear
if errorlevel 1 (
    echo.
    echo [hearth] exited with error - press any key to close...
    pause >nul
)
