@echo off
rem ===========================================================================
rem  Alpha Live Translator -- in-place update
rem
rem  Double-click this file. It updates the installed app without uninstalling
rem  anything. Your API keys, your settings and every saved run log are kept.
rem
rem  It runs apply_update.py with the copy of Python that is already inside the
rem  install, so nothing has to be installed on this machine first and there is
rem  no PowerShell script policy to change.
rem ===========================================================================
setlocal

set "INSTALL=%~1"
if "%INSTALL%"=="" set "INSTALL=%LOCALAPPDATA%\Programs\Alpha Live Translator"

echo.
echo  Alpha Live Translator - update
echo  ==============================
echo  Install folder: %INSTALL%
echo.

if not exist "%INSTALL%\python\python.exe" (
    echo  ERROR: no Alpha Live Translator install found at:
    echo         %INSTALL%
    echo.
    echo  If Alpha is installed somewhere else, drag that folder onto this
    echo  file instead of double-clicking it.
    echo.
    pause
    exit /b 2
)

if not exist "%~dp0app\main.py" (
    echo  ERROR: the update files are missing next to this script.
    echo.
    echo  Extract the WHOLE update folder first, then run this file from the
    echo  extracted folder. Running it straight out of the zip preview does
    echo  not work.
    echo.
    pause
    exit /b 2
)

"%INSTALL%\python\python.exe" "%~dp0apply_update.py" "%INSTALL%" %2 %3
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
    echo  Done. You can start Alpha Live Translator now.
) else (
    echo  The update did NOT complete ^(code %RESULT%^). Nothing was left half-applied.
    echo  Send this window's text to whoever gave you the update.
)
echo.
pause
exit /b %RESULT%
