@echo off
REM CSS Visual Smoke Test
REM Quick visual sanity check after CSS changes (~30 seconds vs full suite)
REM
REM Usage:
REM   scripts\css-smoke-test.bat              # Run smoke tests
REM   scripts\css-smoke-test.bat --update     # Update baseline screenshots

setlocal enabledelayedexpansion

cd /d "%~dp0\.."

echo ==========================================
echo CSS Visual Smoke Tests
echo ==========================================
echo.

REM Check for update flag
set "UPDATE_FLAG="
if "%1"=="--update" set "UPDATE_FLAG=--update-snapshots"
if "%1"=="-u" set "UPDATE_FLAG=--update-snapshots"

if defined UPDATE_FLAG (
  echo Mode: Updating baseline screenshots
) else (
  echo Mode: Comparing against baselines
)
echo.

REM Run the CSS smoke tests
echo Running CSS smoke tests...
echo.

call npx playwright test e2e/css-smoke.spec.ts --reporter=list %UPDATE_FLAG%
if %ERRORLEVEL% EQU 0 (
  echo.
  echo ==========================================
  echo [32m✓ CSS smoke tests passed[0m
  echo ==========================================
  exit /b 0
) else (
  echo.
  echo ==========================================
  echo [31m✗ Visual differences detected![0m
  echo ==========================================
  echo.
  echo To view differences:
  echo   npx playwright show-report
  echo.
  echo To update baselines ^(if changes are intentional^):
  echo   scripts\css-smoke-test.bat --update
  echo.
  exit /b 1
)
