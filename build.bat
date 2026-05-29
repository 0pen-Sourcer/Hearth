@echo off
REM Build the Hearth desktop release bundle.
REM Output lands in dist\Hearth\ - drop the whole folder into a zip and
REM upload to GitHub Releases.

setlocal
set "HERE=%~dp0"
cd /d "%HERE%"

set "PY=%HERE%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo === Cleaning previous build ===
if exist build rmdir /S /Q build
if exist dist  rmdir /S /Q dist

echo === Running PyInstaller ===
"%PY%" -m PyInstaller Hearth.spec --clean --noconfirm
if errorlevel 1 (
    echo PyInstaller failed
    exit /b 1
)

echo === Post-build fixups ===
REM PyInstaller bundles data files under _internal/ - promote the CLI bat to top
if exist "dist\Hearth\_internal\dist_cli_launcher.bat" (
    move /Y "dist\Hearth\_internal\dist_cli_launcher.bat" "dist\Hearth\Hearth-cli.bat" >nul
)

echo.
echo === Build done ===
echo Output: dist\Hearth\
echo   Hearth.exe         (tray + auto-opens window)
echo   Hearth-cli.bat     (CLI in Windows Terminal)
echo   Hearth-cli.exe     (the actual CLI exe)
echo   _HearthWindow.exe  (internal helper for native window)
echo   _internal\         (all deps + voice models + bundled WT)
echo.
dir /B dist\Hearth\
