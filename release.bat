@echo off
REM One-command release cut (Windows). Prefer release.sh on Linux/macOS.
REM Same contract: version already bumped on main → tag → gh release create.
REM CI attaches Windows zip, Linux zip, and AppImage after publish.
REM
REM Usage:
REM   release.bat
REM   release.bat 4.1.0
REM   release.bat --notes _relnotes.txt
REM   release.bat --draft
REM   release.bat --skip-checks
REM   release.bat --yes
REM   release.bat --dry-run

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "NOTES_FILE="
set "DRAFT=0"
set "SKIP_CHECKS=0"
set "YES=0"
set "DRY_RUN=0"
set "WANT_VERSION="

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--notes" (
  set "NOTES_FILE=%~2"
  shift
  shift
  goto parse
)
if /i "%~1"=="--draft" ( set "DRAFT=1" & shift & goto parse )
if /i "%~1"=="--skip-checks" ( set "SKIP_CHECKS=1" & shift & goto parse )
if /i "%~1"=="--yes" ( set "YES=1" & shift & goto parse )
if /i "%~1"=="-y" ( set "YES=1" & shift & goto parse )
if /i "%~1"=="--dry-run" ( set "DRY_RUN=1" & shift & goto parse )
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
echo %~1| findstr /r "^--" >nul && (
  echo unknown flag: %~1
  goto usage
)
if defined WANT_VERSION (
  echo unexpected argument: %~1
  goto usage
)
set "WANT_VERSION=%~1"
shift
goto parse

:usage
echo Usage: release.bat [VERSION] [--notes FILE] [--draft] [--skip-checks] [--yes] [--dry-run]
exit /b 2

:parsed
where git >nul 2>&1 || ( echo error: git is required & exit /b 1 )
where gh  >nul 2>&1 || ( echo error: gh is required & exit /b 1 )
where py  >nul 2>&1 || ( echo error: py launcher is required & exit /b 1 )

for /f "usebackq delims=" %%V in (`py -c "import re,pathlib;t=pathlib.Path('app.py').read_text(encoding='utf-8');m=re.search(r'^__version__\s*=\s*\"([^\"]+)\"',t,re.M);assert m;print(m.group(1))"`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (
  echo error: could not parse __version__ from app.py
  exit /b 1
)

if defined WANT_VERSION (
  set "WANT_VERSION=!WANT_VERSION:v=!"
  if /i not "!WANT_VERSION!"=="!APP_VERSION!" (
    echo error: app.py is !APP_VERSION! but you asked for !WANT_VERSION!
    exit /b 1
  )
)

set "TAG=v!APP_VERSION!"
set "TITLE=Daily Scheduler !TAG!"

for /f "delims=" %%S in ('git status --porcelain') do (
  echo error: working tree is dirty — commit or stash first
  exit /b 1
)

for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD') do set "BRANCH=%%B"
if /i not "!BRANCH!"=="main" (
  echo error: on branch '!BRANCH!' — checkout main
  exit /b 1
)

echo → Fetching origin…
git fetch origin --tags --quiet
if errorlevel 1 exit /b 1

for /f "delims=" %%H in ('git rev-parse HEAD') do set "LOCAL=%%H"
for /f "delims=" %%H in ('git rev-parse origin/main') do set "REMOTE=%%H"
if /i not "!LOCAL!"=="!REMOTE!" (
  echo error: HEAD != origin/main — git pull ^(or push^) first
  exit /b 1
)

git rev-parse "!TAG!" >nul 2>&1 && (
  echo error: tag !TAG! already exists locally
  exit /b 1
)
git ls-remote --tags origin "refs/tags/!TAG!" | findstr /r "." >nul && (
  echo error: tag !TAG! already exists on origin
  exit /b 1
)
gh release view "!TAG!" >nul 2>&1 && (
  echo error: GitHub release !TAG! already exists
  exit /b 1
)

if "!SKIP_CHECKS!"=="0" (
  echo → Syntax check…
  py -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"
  if errorlevel 1 exit /b 1

  where ruff >nul 2>&1 && (
    echo → ruff…
    ruff check app.py --select E9,F63,F7,F82
    if errorlevel 1 exit /b 1
  )

  if exist tests\ (
    echo → Offscreen tests…
    set "QT_QPA_PLATFORM=offscreen"
    set "FAILED=0"
    for %%T in (tests\test_*.py) do (
      echo === %%T ===
      py "%%T" > "%TEMP%\ds_test_out.txt" 2>&1
      type "%TEMP%\ds_test_out.txt"
      findstr /c:"[FAIL]" "%TEMP%\ds_test_out.txt" >nul && set "FAILED=1"
      findstr /r "RESULT: PASS [0-9][0-9]*/[0-9][0-9]* passed" "%TEMP%\ds_test_out.txt" >nul || set "FAILED=1"
    )
    if "!FAILED!"=="1" (
      echo error: tests failed
      exit /b 1
    )
    echo → tests ok
  )
) else (
  echo → Skipping checks ^(--skip-checks^)
)

set "NOTES_TMP="
if not defined NOTES_FILE (
  set "NOTES_TMP=%TEMP%\ds-relnotes-!APP_VERSION!.txt"
  set "PREV="
  for /f "delims=" %%P in ('git describe --tags --abbrev=0 2^>nul') do set "PREV=%%P"
  (
    echo ## What's new in !TAG!
    echo.
    if defined PREV (
      echo Changes since !PREV!:
      echo.
      git log --pretty=format:"- %%s" "!PREV!..HEAD"
      echo.
    ) else (
      echo ^(No previous tag found — add release notes.^)
    )
    echo.
    echo ## Downloads
    echo.
    echo ^| Asset ^| Platform ^|
    echo ^|---^|---^|
    echo ^| `DailyScheduler-win64.zip` ^| Windows 10/11 ^(onedir^) ^|
    echo ^| `DailyScheduler-linux-x86_64.zip` ^| Linux x86_64 ^(onedir, glibc ≥ 2.35^) ^|
    echo ^| `DailyScheduler-x86_64.AppImage` ^| Linux x86_64 ^(single file^) ^|
    echo.
    echo SHA-256 checksums ship alongside each asset ^(`*.sha256`^).
    echo.
    echo CI attaches the binaries a few minutes after this release is published.
  ) > "!NOTES_TMP!"
  set "NOTES_FILE=!NOTES_TMP!"
  if "!YES!"=="0" if "!DRY_RUN!"=="0" (
    echo.
    echo Opening notes in notepad — save and close when done.
    notepad "!NOTES_FILE!"
  )
) else (
  if not exist "!NOTES_FILE!" (
    echo error: notes file not found: !NOTES_FILE!
    exit /b 1
  )
)

echo → Plan:
echo   version : !APP_VERSION!
echo   tag     : !TAG!
for /f "delims=" %%S in ('git log -1 --pretty^=%%h') do set "SHORT=%%S"
for /f "delims=" %%S in ('git log -1 --pretty^=%%s') do set "SUBJ=%%S"
echo   commit  : !SHORT! !SUBJ!
if "!DRAFT!"=="1" ( echo   draft   : yes ) else ( echo   draft   : no )
echo   notes   : !NOTES_FILE!
echo.

if "!DRY_RUN!"=="1" (
  echo → Dry run — no tag, no release.
  exit /b 0
)

if "!YES!"=="0" (
  set /p "ANS=Create and publish !TAG!? [y/N] "
  if /i not "!ANS!"=="y" (
    echo aborted
    exit /b 1
  )
)

echo → Creating annotated tag !TAG!…
git tag -a "!TAG!" -m "!TITLE!"
if errorlevel 1 exit /b 1

echo → Pushing tag to origin…
git push origin "!TAG!"
if errorlevel 1 exit /b 1

if "!DRAFT!"=="1" (
  echo → Creating DRAFT release…
  gh release create "!TAG!" --title "!TITLE!" --notes-file "!NOTES_FILE!" --draft
) else (
  echo → Publishing release ^(triggers CI asset builds^)…
  gh release create "!TAG!" --title "!TITLE!" --notes-file "!NOTES_FILE!" --latest
)
if errorlevel 1 exit /b 1

echo.
echo → Done. Watch assets with:
echo   gh run list --workflow=release-linux.yml --limit 3
echo   gh run list --workflow=release-windows.yml --limit 3
echo   gh release view !TAG!
endlocal
