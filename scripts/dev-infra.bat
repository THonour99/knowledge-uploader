@echo off
setlocal EnableExtensions
title Knowledge Uploader Dev Infra

call "%~dp0load-dev-env.bat"
if errorlevel 1 exit /b 1

echo ============================================================
echo  Knowledge Uploader Dev Infra
echo  Project root: %ROOT%
echo  Env file: %ENV_FILE%
echo ============================================================
echo.

echo [1/3] Checking compose configuration...
docker compose -f "%DEV_COMPOSE_FILE%" -f "%DEV_COMPOSE_OVERRIDE%" config --quiet
if errorlevel 1 (
    echo   [ERROR] Docker Compose configuration is invalid.
    exit /b 1
)
echo   Compose configuration is valid.

if /I "%~1"=="check" (
    echo.
    echo Check mode completed. No containers were started.
    exit /b 0
)

echo.
echo [2/3] Checking Docker...
docker info >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Docker is not running or this terminal cannot access Docker.
    echo           Please start Docker Desktop and make sure Docker CLI can access it.
    exit /b 1
)
echo   Docker is running.

echo.
echo [3/3] Starting PostgreSQL, Redis, RabbitMQ and MinIO...
docker compose -f "%DEV_COMPOSE_FILE%" -f "%DEV_COMPOSE_OVERRIDE%" up -d --wait postgres redis rabbitmq minio
if errorlevel 1 (
    echo   [ERROR] Failed to start development infrastructure.
    exit /b 1
)

echo.
echo Dev infrastructure is ready:
echo   PostgreSQL: 127.0.0.1:%POSTGRES_HOST_PORT%  db=%POSTGRES_DB% user=%POSTGRES_USER%
echo   Redis:      127.0.0.1:%REDIS_HOST_PORT%
echo   RabbitMQ:   127.0.0.1:%RABBITMQ_HOST_PORT%  management=http://127.0.0.1:%RABBITMQ_MANAGEMENT_HOST_PORT%
echo   MinIO:      http://127.0.0.1:%MINIO_CONSOLE_HOST_PORT%
echo.
exit /b 0
