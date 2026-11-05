@echo off
rem docconvert launcher - works after copying the whole folder to any Windows machine
rem with Python 3.8+ installed. No installation step required for the tool itself.

setlocal
set "HERE=%~dp0"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 "%HERE%docconvert.py" %*
    exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python "%HERE%docconvert.py" %*
    exit /b %ERRORLEVEL%
)

echo Python 3.8+ not found in PATH.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
exit /b 1
