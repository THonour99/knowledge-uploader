@echo off
setlocal EnableExtensions
title Knowledge Uploader Worker

call "%~dp0load-dev-env.bat"
if errorlevel 1 exit /b 1

set "MODE=%~1"
if "%MODE%"=="" set "MODE=worker"

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

if /I "%MODE%"=="check" (
    if not exist "%ROOT%\backend\app\workers\celery_app.py" (
        echo [ERROR] Missing backend\app\workers\celery_app.py.
        exit /b 1
    )
    echo Worker check completed.
    exit /b 0
)

if /I "%MODE%"=="deps" (
    "%PYTHON_CMD%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Worker local development requires Python 3.11. Current command is %PYTHON_CMD%.
        echo         Run scripts\dev-setup.bat with Python 3.11 available, or create backend\.venv using Python 3.11.
        exit /b 1
    )
    "%PYTHON_CMD%" -c "import celery" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Celery is missing for %PYTHON_CMD%.
        echo         Run scripts\dev-setup.bat first, or install backend\requirements-dev.txt into backend\.venv.
        exit /b 1
    )
    echo Worker development dependencies are available.
    exit /b 0
)

cd /d "%ROOT%\backend"

if /I "%MODE%"=="outbox" (
    echo Starting outbox dispatcher...
    "%PYTHON_CMD%" -m app.workers.outbox_dispatcher
    exit /b %errorlevel%
)

if /I "%MODE%"=="beat" (
    echo Starting Celery beat scheduler...
    "%PYTHON_CMD%" -m celery -A app.workers.celery_app beat --loglevel=info
    exit /b %errorlevel%
)

if /I not "%MODE%"=="worker" (
    echo [ERROR] Unknown mode: %MODE%
    echo Usage: scripts\dev-worker.bat [worker^|outbox^|beat^|check]
    exit /b 1
)

echo Starting Celery worker with Windows-friendly solo pool...
"%PYTHON_CMD%" -m celery -A app.workers.celery_app worker --loglevel=info --pool=solo --queues=document_queue,ai_queue,ragflow_queue,notification_queue
