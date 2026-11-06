@echo off
rem Double-click to start the local web UI. Keep this window open while using it.
rem Closing the window stops the service.

setlocal
set "HERE=%~dp0"
cd /d "%HERE%"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 docconvert.py serve
    goto :eof
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python docconvert.py serve
    goto :eof
)

echo Python 3.8+ not found. Install from https://www.python.org/downloads/
pause
