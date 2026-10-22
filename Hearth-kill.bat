@echo off
REM Close every running Hearth process: the tray, the window, the terminal app,
REM and the model and image servers they started. Useful when an old instance
REM holds port 8765 and a new one can't start.

echo Closing Hearth...
REM /T takes each process's children with it (llama-server, sd-server, bridges).
taskkill /F /T /IM Hearth.exe        2>nul
taskkill /F /T /IM _HearthWindow.exe 2>nul
taskkill /F /T /IM Hearth-cli.exe    2>nul
echo Done.
echo.
pause
