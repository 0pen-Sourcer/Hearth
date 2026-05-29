@echo off
REM Kill every running Hearth process. Useful when an old tray instance
REM is holding port 8765 and the new build can't bind, or when something
REM stuck in the system tray won't quit cleanly.

echo Killing any running Hearth processes...
taskkill /F /IM Hearth.exe         2>nul
taskkill /F /IM Hearth-window.exe  2>nul
taskkill /F /IM Hearth-cli.exe     2>nul
REM Also nuke any leftover pythonw running our tray module
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq Hearth*" 2>nul
echo Done.
echo.
pause
