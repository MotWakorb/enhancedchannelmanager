@echo off
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   Enhanced Channel Manager - Quality Gates
echo ============================================================
echo.

set FAILED=0

:: Backend Python Syntax Check
echo [*] Checking Python syntax...
python -m py_compile backend\main.py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Python syntax check passed
) else (
    echo [FAIL] Python syntax check FAILED
    python -m py_compile backend\main.py
    set FAILED=1
)

:: Frontend TypeScript Compilation and Build
echo [*] Building frontend (TypeScript + Vite^)...
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

echo.
if %FAILED% EQU 0 (
    echo ============================================================
    echo   All quality gates passed!
    echo ============================================================
    echo.
    exit /b 0
) else (
    echo ============================================================
    echo   Quality gates FAILED! Fix errors before committing.
    echo ============================================================
    echo.
    exit /b 1
)
