@echo off
REM Double-click entry point for the Python end of superweb-client on Windows.
REM
REM Two paths through this script:
REM   1. Non-elevated (the user double-clicks): finds host Python, runs
REM      setup.py to ensure python\.venv is current, then re-spawns this same
REM      script under UAC so the elevated branch can install the temporary
REM      UDP 5353 firewall rules used for mDNS discovery.
REM   2. Elevated (after UAC accept): hands off to terminal\interactive.py
REM      using the venv Python.
REM
REM setup.py is idempotent (hash-stamped against requirements.txt), so repeated
REM double-clicks only pay the real cost on the first run or after a
REM requirements change.

setlocal
pushd "%~dp0"
chcp 65001 >NUL

net session >NUL 2>&1
if not errorlevel 1 goto :launch

REM ---- Non-elevated branch ------------------------------------------------

set "HOST_PY="
where py >NUL 2>&1 && set "HOST_PY=py -3"
if not defined HOST_PY (
    where python >NUL 2>&1 && set "HOST_PY=python"
)
if not defined HOST_PY (
    echo [ERROR] No Python interpreter found on PATH.
    echo Install Python 3.10 or newer from https://www.python.org/downloads/
    echo and rerun this launcher.
    pause
    popd
    endlocal
    exit /b 1
)

echo [run.cmd] Running %HOST_PY% setup.py to prepare python\.venv...
%HOST_PY% -X utf8 setup.py
if errorlevel 1 (
    echo [ERROR] setup.py exited with code %errorlevel%. Fix the errors above
    echo and rerun this launcher.
    pause
    popd
    endlocal
    exit /b %errorlevel%
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Expected .venv\Scripts\python.exe to exist after setup, but
    echo it is missing.
    pause
    popd
    endlocal
    exit /b 1
)

echo [run.cmd] Requesting administrator privileges for temporary mDNS firewall rules...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%CD%' -Verb RunAs"
popd
endlocal
exit /b 0

REM ---- Elevated branch ----------------------------------------------------

:launch
set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] %VENV_PY% does not exist. Run this script first without
    echo admin so it can set up python\.venv.
    pause
    popd
    endlocal
    exit /b 1
)

set "VERBOSE_ARG="
set /p VERBOSE_CHOICE="Enable verbose logging for this session? (writes Client\python\logs\windows-terminal-*.txt) [y/N]: "
if /I "%VERBOSE_CHOICE%"=="y" set "VERBOSE_ARG=--verbose"
if /I "%VERBOSE_CHOICE%"=="yes" set "VERBOSE_ARG=--verbose"

"%VENV_PY%" bootstrap.py %VERBOSE_ARG%
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
    echo.
    echo superweb-client exited with code %EXIT_CODE%.
    pause
)

popd
endlocal
exit /b %EXIT_CODE%
