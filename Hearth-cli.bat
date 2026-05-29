@echo off
REM Hearth-cli launcher. Opens the bundled CLI exe inside Windows Terminal
REM (portable, bundled in the same dist/) for a tabbed experience.

setlocal
set "HERE=%~dp0"

REM Where is the CLI exe? Two possibilities:
REM   - "release" tree:  HERE\dist\Hearth\Hearth-cli.exe   (when run from repo root)
REM   - "release" tree:  HERE\Hearth-cli.exe                (when run from dist/Hearth)
REM   - "dev" tree:      no exe - fall through to hearth.bat (which uses venv python)
set "CLI=%HERE%dist\Hearth\Hearth-cli.exe"
if exist "%CLI%" goto have_cli
set "CLI=%HERE%Hearth-cli.exe"
if exist "%CLI%" goto have_cli

REM Dev mode - no exe yet, use hearth.bat directly
call "%HERE%hearth.bat" %*
exit /b

:have_cli
REM Find a wt.exe — prefer the one next to the CLI exe (bundled in dist),
REM then the one in the repo root, then system PATH.
set "EXE_DIR=%CLI%\.."
for %%I in ("%EXE_DIR%") do set "EXE_DIR=%%~fI"
set "WT=%EXE_DIR%\_internal\Windows Terminal\wt.exe"
if exist "%WT%" goto launch_wt
set "WT=%HERE%Windows Terminal\wt.exe"
if exist "%WT%" goto launch_wt
where wt >nul 2>nul
if %errorlevel%==0 (
    start "" wt --title "Hearth" "%CLI%" %*
    exit /b
)
REM Fallback: no terminal — direct
"%CLI%" %*
exit /b

:launch_wt
start "" "%WT%" --title "Hearth" "%CLI%" %*
exit /b
