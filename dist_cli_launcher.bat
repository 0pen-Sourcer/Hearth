@echo off
REM Hearth CLI launcher — bundled with dist/Hearth/.
REM Launches Hearth-cli.exe inside the bundled portable Windows Terminal
REM (or system wt, or legacy console as fallback).

setlocal
set "HERE=%~dp0"
set "CLI=%HERE%Hearth-cli.exe"

if not exist "%CLI%" (
    echo Hearth-cli.exe not found next to this bat.
    pause
    exit /b 1
)

REM Prefer the portable WT we bundled at _internal\Windows Terminal\wt.exe
set "WT=%HERE%_internal\Windows Terminal\wt.exe"
if exist "%WT%" goto launch_bundled_wt

REM Fallback to system WT if installed
where wt >nul 2>nul
if %errorlevel%==0 goto launch_system_wt

REM Last resort: legacy console
"%CLI%" %*
exit /b

:launch_bundled_wt
start "" "%WT%" --title "Hearth" "%CLI%" %*
exit /b

:launch_system_wt
start "" wt --title "Hearth" "%CLI%" %*
exit /b
