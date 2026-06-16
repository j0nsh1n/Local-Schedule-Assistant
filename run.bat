@echo off
echo ============================================================
echo   Daily Scheduler  —  Native Desktop App (Python + Qt6)
echo ============================================================
echo.

REM Try py launcher first (Windows standard), then fall back to python
where py >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=py
) else (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        set PYTHON=python
    ) else (
        echo ERROR: Python not found. Install from https://www.python.org/
        pause
        exit /b 1
    )
)

echo Installing / verifying dependencies…
%PYTHON% -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo.
    echo ERROR: pip install failed. See output above.
    pause
    exit /b 1
)

echo.
echo Starting Daily Scheduler…
echo.
%PYTHON% app.py
pause
