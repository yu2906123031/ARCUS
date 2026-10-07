@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "MM_PYTHON=C:\Python312\python.exe"
if exist ".venv\Scripts\python.exe" set "MM_PYTHON=%~dp0.venv\Scripts\python.exe"
"%MM_PYTHON%" -u -m arcus_mm.compare --flatten-on-exit
pause
