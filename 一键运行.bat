@echo off
setlocal
cd /d "%~dp0"
title ARCUS account1 SPY-USD LIVE maker
set "PYTHONUTF8=1"
set "MM_PYTHON="
if exist "%~dp0.venv\Scripts\python.exe" set "MM_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined MM_PYTHON if exist "C:\Python312\python.exe" set "MM_PYTHON=C:\Python312\python.exe"
if not defined MM_PYTHON for %%P in (python.exe) do set "MM_PYTHON=%%~$PATH:P"
if not defined MM_PYTHON (
    echo Python 3.12 was not found. Install Python 3.12 and try again.
    set "MM_EXIT=1"
    goto finish
)
"%MM_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)"
if errorlevel 1 (
    echo This launcher requires Python 3.12.
    set "MM_EXIT=1"
    goto finish
)
"%MM_PYTHON%" -c "import httpx, websockets.asyncio.client, cryptography" >nul 2>&1
if errorlevel 1 (
    echo Installing project dependencies...
    "%MM_PYTHON%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        set "MM_EXIT=1"
        goto finish
    )
)
if /I "%~1"=="--check" goto doctor
if not "%~1"=="" (
    echo Only --check is accepted. This launcher always uses LIVE mode.
    set "MM_EXIT=1"
    goto finish
)
echo ARCUS account1 SPY-USD LIVE trading - real mainnet orders.
echo Settings: mm_live_spy_account1.json. Orders: USD 100-150. Leverage: 2x.
echo Unclassified order denials: restart after verified cleanup. Default cooldown: 60s.
echo Run time: until Ctrl+C. Logs: mm_logs/account1-SPY-USD. Credentials: .env.
echo Protection: server fallback enabled with local risk controls.
echo Ctrl+C requests a stop and reduce-only flattening. Do not close this window.
"%MM_PYTHON%" -u -m arcus_mm run --config mm_live_spy_account1.json --live --auto-resume --auto-restart --seconds 0 --flatten-on-exit
set "MM_EXIT=%ERRORLEVEL%"
goto finish
:doctor
"%MM_PYTHON%" -u -m arcus_mm doctor --config mm_live_spy_account1.json --live --auto-resume
set "MM_EXIT=%ERRORLEVEL%"
:finish
echo.
if "%MM_EXIT%"=="0" (echo Completed.) else (echo Stopped with error. Review mm_logs. Exit code: %MM_EXIT%)
if /I not "%~1"=="--check" pause
exit /b %MM_EXIT%
