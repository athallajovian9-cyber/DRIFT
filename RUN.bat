@echo off
setlocal enabledelayedexpansion
title DRIFT

rem ===================================================================
rem  DRIFT - is this machine getting worse, and what is doing it.
rem
rem  Double-click this file for a reading. To see the stored history
rem  instead, run:   RUN.bat show
rem  The boot-time trend alone:  RUN.bat boots
rem  You can pass any command through:  RUN.bat where
rem ===================================================================

cd /d "%~dp0"

set "EXE=%~dp0DRIFT.exe"
set "PY=%~dp0drift.py"
set "CMD=%*"
if "%CMD%"=="" set "CMD=run"

rem the packaged build first, then a Python on PATH
if exist "%EXE%" (
    "%EXE%" %CMD%
    set "RC=!errorlevel!"
    goto done
)

where python >nul 2>&1
if !errorlevel! equ 0 (
    python "%PY%" %CMD%
    set "RC=!errorlevel!"
    goto done
)

where py >nul 2>&1
if !errorlevel! equ 0 (
    py "%PY%" %CMD%
    set "RC=!errorlevel!"
    goto done
)

echo.
echo  DRIFT needs either DRIFT.exe next to this file, or Python on PATH.
echo  Neither was found.
echo.
echo  The packaged DRIFT.exe is the normal way to run this. If you are
echo  running from source instead, install Python from python.org and
echo  make sure "Add python.exe to PATH" is ticked during setup.
echo.
pause
exit /b 2

:done
rem Only hold the window open on a double-click. When run from a
rem command prompt with arguments, get out of the way.
if "%~1"=="" (
    echo.
    pause
)
exit /b !RC!
