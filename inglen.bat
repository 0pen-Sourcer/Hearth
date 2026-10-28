@echo off
REM Inglen from a source checkout.
REM   inglen          the terminal app
REM   inglen window   the desktop window (tray app)
REM hearth.bat and "python -m hearth.tray --open" still work; this is the same thing under the app's name.

setlocal
set "HEARTH_HOME=%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=python"
if exist "%HEARTH_HOME%.venv\Scripts\python.exe" set "PY=%HEARTH_HOME%.venv\Scripts\python.exe"

if /i "%~1"=="window" (
    cd /d "%HEARTH_HOME%"
    "%PY%" -m hearth.tray --open
    exit /b %errorlevel%
)
call "%HEARTH_HOME%hearth.bat" %*
