@echo off
setlocal DisableDelayedExpansion
REM Python here only bootstraps shared project-interpreter selection and policy.
set "BOOTSTRAP=%ECM_PYTHON%"
if defined BOOTSTRAP goto run
set "BOOTSTRAP=%~dp0..\.venv\Scripts\python.exe"
if exist "%BOOTSTRAP%" goto run
set "BOOTSTRAP=python"
:run
"%BOOTSTRAP%" "%~dp0gate_runner.py" --quality %*
exit /b %ERRORLEVEL%
