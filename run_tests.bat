@echo off
setlocal
cd /d "%~dp0"
python -m unittest -v
set "TEST_EXIT_CODE=%ERRORLEVEL%"
echo.
if "%TEST_EXIT_CODE%"=="0" (
    echo All tests passed.
) else (
    echo Tests failed. Exit code: %TEST_EXIT_CODE%
)
echo.
pause
exit /b %TEST_EXIT_CODE%
