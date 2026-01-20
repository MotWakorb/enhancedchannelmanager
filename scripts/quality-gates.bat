@echo off
setlocal enabledelayedexpansion

REM
REM Quality Gates Script (Windows)
REM
REM Runs all quality checks before committing code:
REM - Backend: Python syntax check and pytest
REM - Frontend: Vitest unit tests and TypeScript compilation
REM - E2E: Playwright tests (optional, requires RUN_E2E=1)
REM
REM Usage:
REM   .scripts\quality-gates.bat          # Run all checks except E2E
REM   set RUN_E2E=1 && .scripts\quality-gates.bat  # Include E2E tests
REM
REM Exit codes:
REM   0 - All checks passed
REM   1 - One or more checks failed
REM

echo.
echo ============================================================
echo   Enhanced Channel Manager - Quality Gates
echo ============================================================
echo.

set FAILED=0

REM ----------------------------------------------------------------
REM Backend Checks
REM ----------------------------------------------------------------
echo.
echo [Backend Checks]
echo ------------------------------------------------------------

:: Backend Python Syntax Check
echo [*] Python syntax check (main.py^)...
python -m py_compile backend\main.py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Python syntax check passed
) else (
    echo [FAIL] Python syntax check FAILED
    python -m py_compile backend\main.py
    set FAILED=1
)

:: Backend Unit Tests (pytest)
if exist backend\tests (
    echo [*] Backend unit tests (pytest^)...
    cd backend
    python -m pytest tests/ -q --tb=short >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [OK] Backend unit tests passed
    ) else (
        echo [FAIL] Backend unit tests FAILED
        python -m pytest tests/ -q --tb=short
        set FAILED=1
    )
    cd ..
) else (
    echo [SKIP] Backend unit tests (no tests directory^)
)

REM ----------------------------------------------------------------
REM Frontend Checks
REM ----------------------------------------------------------------
echo.
echo [Frontend Checks]
echo ------------------------------------------------------------

:: Frontend Unit Tests (vitest)
if exist frontend\src (
    echo [*] Frontend unit tests (vitest^)...
    cd frontend
    call npm test >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [OK] Frontend unit tests passed
    ) else (
        echo [FAIL] Frontend unit tests FAILED
        call npm test
        set FAILED=1
    )
    cd ..
) else (
    echo [SKIP] Frontend unit tests
)

:: Frontend TypeScript Compilation and Build
echo [*] Frontend build (vite build^)...
cd frontend
call npm run build >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Frontend build passed
) else (
    echo [FAIL] Frontend build FAILED
    call npm run build
    set FAILED=1
)
cd ..

REM ----------------------------------------------------------------
REM E2E Tests (Optional)
REM ----------------------------------------------------------------
echo.
echo [E2E Tests]
echo ------------------------------------------------------------

if "%RUN_E2E%"=="1" (
    if exist e2e (
        echo [*] E2E tests (Playwright^)...
        call npm run test:e2e >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            echo [OK] E2E tests passed
        ) else (
            echo [FAIL] E2E tests FAILED
            call npm run test:e2e
            set FAILED=1
        )
    ) else (
        echo [SKIP] E2E tests (no e2e directory^)
    )
) else (
    echo [SKIP] E2E tests (set RUN_E2E=1 to enable^)
)

echo.
echo ============================================================
if %FAILED% EQU 0 (
    echo   All quality gates passed!
    echo ============================================================
    echo.
    exit /b 0
) else (
    echo   Quality gates FAILED! Fix errors before committing.
    echo ============================================================
    echo.
    exit /b 1
)
