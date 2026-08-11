@echo off
REM Build the Windows --onedir package and zip it for a GitHub Release.
REM Kill any running DailyScheduler.exe first or PyInstaller will fail to overwrite.
REM Usage (from the repo root, on the Windows box):
REM   build-windows.bat
REM Then attach dist_exe\DailyScheduler-win64.zip to the release.

setlocal
cd /d "%~dp0"

py -m pip install -q pyinstaller -r requirements.txt
if errorlevel 1 exit /b 1

REM PySide6 imports shiboken6 at startup for DLL search paths. On Windows a
REM freeze that only --collect-all PySide6 can leave shiboken6 out of _internal,
REM which crashes at launch with:
REM   ImportError: ...\shiboken6 does not exist
REM (bootloader text: "Failed to execute script 'app'" — the entry module is app.py).
py -m PyInstaller --noconfirm --onedir --windowed --name DailyScheduler ^
  --collect-all PySide6 ^
  --collect-all shiboken6 ^
  --hidden-import shiboken6 ^
  --copy-metadata PySide6 ^
  --copy-metadata shiboken6 ^
  --add-data "LICENSE;." ^
  --distpath dist_exe --workpath build --specpath . ^
  app.py
if errorlevel 1 exit /b 1

REM Zip the whole folder (exe + _internal). PowerShell is always available on Win10+.
if exist "dist_exe\DailyScheduler-win64.zip" del /f "dist_exe\DailyScheduler-win64.zip"
powershell -NoProfile -Command ^
  "Compress-Archive -Path 'dist_exe\DailyScheduler' -DestinationPath 'dist_exe\DailyScheduler-win64.zip' -Force"
if errorlevel 1 exit /b 1

echo.
echo Built: dist_exe\DailyScheduler\DailyScheduler.exe
echo Zip:   dist_exe\DailyScheduler-win64.zip
echo Attach the zip to the GitHub release (not a single .exe).
endlocal
