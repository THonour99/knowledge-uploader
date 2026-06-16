@echo off
setlocal EnableExtensions
title Knowledge Uploader Dev Launcher

call "%~dp0load-dev-env.bat"
if errorlevel 1 exit /b 1

set "START_WORKERS=0"
set "CHECK_ONLY=0"

if /I "%~1"=="worker" set "START_WORKERS=1"
if /I "%~1"=="workers" set "START_WORKERS=1"
if /I "%~1"=="all" set "START_WORKERS=1"
if /I "%~1"=="check" set "CHECK_ONLY=1"

echo ============================================================
echo  Knowledge Uploader Dev Environment
echo  Project root: %ROOT%
echo  Env file: %ENV_FILE%
echo ============================================================
echo.

if "%CHECK_ONLY%"=="1" (
    call "%ROOT%\scripts\load-dev-env.bat" check
    if errorlevel 1 exit /b 1
    call "%ROOT%\scripts\dev-infra.bat" check
    if errorlevel 1 exit /b 1
    call "%ROOT%\scripts\dev-api.bat" check
    if errorlevel 1 exit /b 1
    call "%ROOT%\scripts\dev-web.bat" check
    if errorlevel 1 exit /b 1
    call "%ROOT%\scripts\dev-worker.bat" check
    if errorlevel 1 exit /b 1
    call "%ROOT%\scripts\dev-setup.bat" check
    if errorlevel 1 exit /b 1
    call "%ROOT%\scripts\dev-stop.bat" check
    if errorlevel 1 exit /b 1
    call "%ROOT%\scripts\dev-check.bat" check
    if errorlevel 1 exit /b 1
    echo.
    echo Dev script checks passed.
    exit /b 0
)

call "%ROOT%\scripts\dev-api.bat" deps
if errorlevel 1 (
    echo.
    echo Local backend dependencies are not ready. Running scripts\dev-setup.bat...
    call "%ROOT%\scripts\dev-setup.bat"
    if errorlevel 1 (
        echo.
        echo [ERROR] Local development setup failed.
        pause
        exit /b 1
    )
    call "%ROOT%\scripts\dev-api.bat" deps
    if errorlevel 1 (
        echo.
        echo [ERROR] Local backend dependencies are still not ready after setup.
        pause
        exit /b 1
    )
)

call "%ROOT%\scripts\dev-web.bat" deps
if errorlevel 1 (
    echo.
    echo Local frontend dependencies are not ready. Running scripts\dev-setup.bat...
    call "%ROOT%\scripts\dev-setup.bat"
    if errorlevel 1 (
        echo.
        echo [ERROR] Local development setup failed.
        pause
        exit /b 1
    )
    call "%ROOT%\scripts\dev-web.bat" deps
    if errorlevel 1 (
        echo.
        echo [ERROR] Local frontend dependencies are still not ready after setup.
        pause
        exit /b 1
    )
)

if "%START_WORKERS%"=="1" (
    call "%ROOT%\scripts\dev-worker.bat" deps
    if errorlevel 1 (
        echo.
        echo Local worker dependencies are not ready. Running scripts\dev-setup.bat...
        call "%ROOT%\scripts\dev-setup.bat"
        if errorlevel 1 (
            echo.
            echo [ERROR] Local development setup failed.
            pause
            exit /b 1
        )
        call "%ROOT%\scripts\dev-worker.bat" deps
        if errorlevel 1 (
            echo.
            echo [ERROR] Local worker dependencies are still not ready after setup.
            pause
            exit /b 1
        )
    )
)

call "%ROOT%\scripts\dev-infra.bat"
if errorlevel 1 (
    echo.
    echo [ERROR] Development infrastructure failed to start.
    pause
    exit /b 1
)

echo.
echo Freeing Docker app containers that conflict with local dev ports...
docker compose -f "%DEV_COMPOSE_FILE%" -f "%DEV_COMPOSE_OVERRIDE%" stop nginx frontend backend-api outbox-dispatcher worker-document worker-ai worker-ragflow worker-statistics worker-notification scheduler >nul 2>&1

echo.
echo Running database migrations with local backend environment...
cd /d "%ROOT%\backend"
set "PYTHON_CMD=python"
if exist "%ROOT%\backend\.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%ROOT%\backend\.venv\Scripts\python.exe"
)
"%PYTHON_CMD%" -m alembic upgrade head
if errorlevel 1 (
    echo.
    echo [ERROR] Alembic migration failed. Check local backend dependencies and database connectivity.
    pause
    exit /b 1
)

echo.
echo Launching local development windows...
start "Knowledge API :%BACKEND_API_PORT%" cmd /k call "%ROOT%\scripts\dev-api.bat"

echo Waiting for API on 127.0.0.1:%BACKEND_API_PORT% before starting web...
set "API_READY=0"
for /L %%I in (1,1,120) do (
    netstat -ano -p tcp | findstr /R /C:":%BACKEND_API_PORT% .*LISTENING" >nul 2>&1
    if not errorlevel 1 (
        set "API_READY=1"
        goto api_ready
    )
    ping -n 2 127.0.0.1 >nul
)

:api_ready
if "%API_READY%"=="1" (
    echo API is listening.
) else (
    echo [WARN] API did not listen on 127.0.0.1:%BACKEND_API_PORT% within 120 seconds.
    echo        Check the "Knowledge API :%BACKEND_API_PORT%" window for the startup error.
    echo        Starting web anyway so the frontend window remains available.
)

start "Knowledge Web :%FRONTEND_HTTP_PORT%" cmd /k call "%ROOT%\scripts\dev-web.bat"

if "%START_WORKERS%"=="1" (
    start "Knowledge Outbox Dispatcher" cmd /k call "%ROOT%\scripts\dev-worker.bat" outbox
    start "Knowledge Celery Worker" cmd /k call "%ROOT%\scripts\dev-worker.bat" worker
    start "Knowledge Celery Beat" cmd /k call "%ROOT%\scripts\dev-worker.bat" beat
)

echo.
echo ============================================================
echo  Dev environment is starting.
echo ------------------------------------------------------------
echo   Web:       http://127.0.0.1:%FRONTEND_HTTP_PORT%
echo   API:       http://127.0.0.1:%BACKEND_API_PORT%
echo   API Docs:  http://127.0.0.1:%BACKEND_API_PORT%/docs
echo   Ready:     http://127.0.0.1:%BACKEND_API_PORT%/api/system/ready
echo   RabbitMQ:  http://127.0.0.1:%RABBITMQ_MANAGEMENT_HOST_PORT%
echo   MinIO:     http://127.0.0.1:%MINIO_CONSOLE_HOST_PORT%
if "%START_WORKERS%"=="1" (
echo   Workers:   outbox, celery worker and beat windows started
) else (
echo   Workers:   optional, run scripts\dev.bat worker when needed
)
echo ------------------------------------------------------------
echo  To stop local API/Web/Workers: close the opened windows.
echo  To stop PostgreSQL/Redis/RabbitMQ/MinIO: scripts\dev-stop.bat
echo  To run Docker verification before deploy: scripts\dev-check.bat
echo ============================================================
echo.
pause
