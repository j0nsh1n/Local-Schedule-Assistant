@echo off
echo ============================================================
echo   Daily Scheduler  —  Native Desktop App (Python + Qt6)
echo ============================================================
echo.

REM Project pin: Python 3.14 (see .python-version / spec.md).
where py >nul 2>&1
if %errorlevel% equ 0 (
    py -3.14 -c "import sys" >nul 2>&1
    if %errorlevel% equ 0 goto :use_py314
    echo WARNING: Python 3.14 not found via py launcher; using default py.
    goto :use_py
)
where python >nul 2>&1
if %errorlevel% equ 0 (
    echo WARNING: py launcher missing; using python on PATH (need 3.14).
    goto :use_python
)
echo ERROR: Python not found. Install Python 3.14 from https://www.python.org/
pause
exit /b 1

:use_py314
echo Installing / verifying dependencies…
py -3.14 -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 goto :pip_fail
echo.
echo Starting Daily Scheduler…
echo.
py -3.14 app.py
pause
exit /b 0

:use_py
echo Installing / verifying dependencies…
py -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 goto :pip_fail
echo.
echo Starting Daily Scheduler…
echo.
py app.py
pause
exit /b 0

:use_python
echo Installing / verifying dependencies…
python -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 goto :pip_fail
echo.
echo Starting Daily Scheduler…
echo.
python app.py
pause
exit /b 0

:pip_fail
echo.
echo ERROR: pip install failed. See output above.
pause
exit /b 1
