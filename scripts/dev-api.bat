@echo off
setlocal EnableExtensions
title Knowledge Uploader API

call "%~dp0load-dev-env.bat"
if errorlevel 1 exit /b 1

set "PYTHON_CMD=python"
if exist "%ROOT%\backend\.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%ROOT%\backend\.venv\Scripts\python.exe"
)

where.exe "%PYTHON_CMD%" >nul 2>nul
if errorlevel 1 (
    if not exist "%PYTHON_CMD%" (
        echo [ERROR] Python was not found. Install Python 3.11 or create backend\.venv.
        exit /b 1
    )
)

if /I "%~1"=="check" (
    echo Python command: %PYTHON_CMD%
    if not exist "%ROOT%\backend\app\main.py" (
        echo [ERROR] Missing backend\app\main.py.
        exit /b 1
    )
    if not exist "%ROOT%\backend\alembic.ini" (
        echo [ERROR] Missing backend\alembic.ini.
        exit /b 1
    )
    echo API check completed.
    exit /b 0
)

if /I "%~1"=="deps" (
    "%PYTHON_CMD%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Backend local development requires Python 3.11. Current command is %PYTHON_CMD%.
        echo         Run scripts\dev-setup.bat with Python 3.11 available, or create backend\.venv using Python 3.11.
        exit /b 1
    )
    "%PYTHON_CMD%" -c "import alembic, asyncpg, psycopg, uvicorn" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Backend development dependencies are missing for %PYTHON_CMD%.
        echo         Run scripts\dev-setup.bat first, or install backend\requirements-dev.txt into backend\.venv.
        exit /b 1
    )
    echo Backend development dependencies are available.
    exit /b 0
)

echo ============================================================
echo  Knowledge Uploader API
echo  API:   http://127.0.0.1:%BACKEND_API_PORT%
echo  Ready: http://127.0.0.1:%BACKEND_API_PORT%/api/system/ready
echo  Docs:  http://127.0.0.1:%BACKEND_API_PORT%/docs
echo ============================================================
echo.

cd /d "%ROOT%\backend"
"%PYTHON_CMD%" -m uvicorn app.main:app --reload --host 127.0.0.1 --port %BACKEND_API_PORT%
