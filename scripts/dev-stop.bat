@echo off
setlocal EnableExtensions
title Knowledge Uploader Dev Stop

call "%~dp0load-dev-env.bat"
if errorlevel 1 exit /b 1

if /I "%~1"=="check" (
    docker compose -f "%DEV_COMPOSE_FILE%" -f "%DEV_COMPOSE_OVERRIDE%" config --quiet
    exit /b %errorlevel%
)

echo Stopping development infrastructure containers...
docker compose -f "%DEV_COMPOSE_FILE%" -f "%DEV_COMPOSE_OVERRIDE%" stop postgres redis rabbitmq minio
exit /b %errorlevel%
