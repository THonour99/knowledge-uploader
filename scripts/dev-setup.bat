@echo off
setlocal EnableExtensions
title Knowledge Uploader Dev Setup

call "%~dp0load-dev-env.bat"
if errorlevel 1 exit /b 1

if /I "%~1"=="check" (
    if not exist "%ROOT%\backend\requirements-dev.txt" (
        echo [ERROR] Missing backend\requirements-dev.txt.
        exit /b 1
    )
    if not exist "%ROOT%\frontend\package.json" (
        echo [ERROR] Missing frontend\package.json.
        exit /b 1
    )
    echo Dev setup check completed.
    exit /b 0
)

set "PYTHON_BOOTSTRAP=python"
py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_BOOTSTRAP=py -3.11"
) else (
    where.exe python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found. Install Python 3.11 and reopen this terminal.
        exit /b 1
    )
    python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python 3.11 was not found. Install Python 3.11 or make py -3.11 available.
        exit /b 1
    )
)

where.exe npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm was not found. Install Node.js 20.19+ and reopen this terminal.
    exit /b 1
)

echo ============================================================
echo  Knowledge Uploader Dev Setup
echo ============================================================
echo.

if not exist "%ROOT%\backend\.venv\Scripts\python.exe" (
    echo Creating backend virtual environment...
    %PYTHON_BOOTSTRAP% -m venv "%ROOT%\backend\.venv"
    if errorlevel 1 exit /b 1
)

echo Installing backend dependencies...
"%ROOT%\backend\.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%ROOT%\backend\.venv\Scripts\python.exe" -m pip install -r "%ROOT%\backend\requirements-dev.txt"
if errorlevel 1 exit /b 1

echo.
echo Installing frontend dependencies...
cd /d "%ROOT%\frontend"
npm install
if errorlevel 1 exit /b 1

echo.
echo Development dependencies are ready.
echo Run scripts\dev.bat to start local development.
exit /b 0
