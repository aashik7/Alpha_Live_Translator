@echo off
setlocal EnableExtensions
title Alpha Live Translator - collect logs

rem Collect every diagnostic log into a folder named "logs" beside THIS file.
rem
rem Same collector the app's Start Menu shortcut and the documented PowerShell
rem one-liner use (app\collect_logs.py), run with:
rem     --runs 0      every session ever recorded, not just the newest five
rem     --with-audio  the recorded audio too, so nothing is left out
rem     --no-dialog   no message box; this window reports instead
rem `.env` is never collected and anything that looks like an API key is
rem redacted from every file that goes in, so the bundle is safe to send.
rem
rem Works next to the portable folder, inside it, or with nothing but an
rem installed copy on the machine.

set "OUT=%~dp0logs"
set "APP="

call :try "%~dp0Alpha Live Translator"
call :try "%~dp0."
call :try "%~dp0.."
call :try "%LOCALAPPDATA%\Programs\Alpha Live Translator"
call :try "%USERPROFILE%\Alpha Live Translator"
call :try "%USERPROFILE%\Desktop\Alpha Live Translator"
call :try "%USERPROFILE%\Downloads\Alpha Live Translator"
call :try "%USERPROFILE%\Documents\Alpha Live Translator"

if not defined APP (
  echo.
  echo Could not find Alpha Live Translator.
  echo.
  echo Put this file next to the app folder ^(the one containing "app" and
  echo "python"^), or install the app, then run it again.
  echo.
  pause
  exit /b 1
)

echo Found the app at:
echo   %APP%
echo.

set "PY=%APP%\python\python.exe"
if not exist "%PY%" set "PY=py"

if not exist "%OUT%" mkdir "%OUT%"

echo Collecting every log ^(all sessions, audio included^). This can take a minute...
echo.
"%PY%" "%APP%\app\collect_logs.py" --runs 0 --with-audio --no-dialog --out "%OUT%"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo Collection FAILED ^(exit code %RC%^). Nothing was written.
  echo.
  pause
  exit /b %RC%
)

echo Done. The bundle is in:
echo   %OUT%
echo.
echo Send the newest AlphaLogs-*.zip from that folder.
echo.
start "" "%OUT%"
pause
exit /b 0

:try
rem Accept a folder only when the collector is actually inside it.
if defined APP goto :eof
if exist "%~1\app\collect_logs.py" set "APP=%~f1"
goto :eof
