@echo off
rem Shared helper for Windows local development scripts. Call this file; do not run it directly.

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

set "ENV_FILE=%ROOT%\.env"
if not exist "%ENV_FILE%" (
    set "ENV_FILE=%ROOT%\.env.example"
)

if not exist "%ENV_FILE%" (
    echo [ERROR] Missing .env and .env.example in %ROOT%.
    exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if not "%%~A"=="" set "%%~A=%%~B"
)

if not defined APP_ENV set "APP_ENV=development"
if not defined APP_BASE_URL set "APP_BASE_URL=http://127.0.0.1:5173"
if not defined JWT_SECRET set "JWT_SECRET=change-me-change-me-change-me-change-me"
if not defined ENCRYPTION_KEY set "ENCRYPTION_KEY=RZ1Sw_27VrN9c5Cfsq01qiwViwT6y7jDCuXYn7tgGJY="
if not defined BACKEND_API_HOST set "BACKEND_API_HOST=127.0.0.1"
if not defined BACKEND_API_PORT set "BACKEND_API_PORT=18000"
if not defined FRONTEND_HTTP_PORT set "FRONTEND_HTTP_PORT=5173"
if not defined POSTGRES_PORT set "POSTGRES_PORT=5432"
if not defined POSTGRES_HOST_PORT set "POSTGRES_HOST_PORT=15432"
if not defined POSTGRES_DB set "POSTGRES_DB=knowledge_uploader"
if not defined POSTGRES_USER set "POSTGRES_USER=knowledge"
if not defined POSTGRES_PASSWORD set "POSTGRES_PASSWORD=knowledge_password"
if not defined RABBITMQ_PORT set "RABBITMQ_PORT=5672"
if not defined RABBITMQ_HOST_PORT set "RABBITMQ_HOST_PORT=15673"
if not defined RABBITMQ_MANAGEMENT_HOST_PORT set "RABBITMQ_MANAGEMENT_HOST_PORT=15672"
if not defined RABBITMQ_USER set "RABBITMQ_USER=knowledge"
if not defined RABBITMQ_PASSWORD set "RABBITMQ_PASSWORD=knowledge_password"
if not defined REDIS_PORT set "REDIS_PORT=6379"
if not defined REDIS_HOST_PORT set "REDIS_HOST_PORT=16379"
if not defined MINIO_ACCESS_KEY set "MINIO_ACCESS_KEY=knowledge"
if not defined MINIO_SECRET_KEY set "MINIO_SECRET_KEY=knowledge_password"
if not defined MINIO_BUCKET set "MINIO_BUCKET=knowledge-files"
if not defined MINIO_API_HOST_PORT set "MINIO_API_HOST_PORT=19000"
if not defined MINIO_CONSOLE_HOST_PORT set "MINIO_CONSOLE_HOST_PORT=19001"

rem Local app processes must use host ports, not Docker network hostnames.
set "POSTGRES_HOST=127.0.0.1"
set "RABBITMQ_HOST=127.0.0.1"
set "REDIS_HOST=127.0.0.1"
set "MINIO_ENDPOINT=127.0.0.1:%MINIO_API_HOST_PORT%"
set "DATABASE_URL=postgresql+asyncpg://%POSTGRES_USER%:%POSTGRES_PASSWORD%@127.0.0.1:%POSTGRES_HOST_PORT%/%POSTGRES_DB%"
set "ALEMBIC_DATABASE_URL=postgresql+psycopg://%POSTGRES_USER%:%POSTGRES_PASSWORD%@127.0.0.1:%POSTGRES_HOST_PORT%/%POSTGRES_DB%"
set "CELERY_BROKER_URL=amqp://%RABBITMQ_USER%:%RABBITMQ_PASSWORD%@127.0.0.1:%RABBITMQ_HOST_PORT%//"
if defined REDIS_PASSWORD (
    set "CELERY_RESULT_BACKEND=redis://:%REDIS_PASSWORD%@127.0.0.1:%REDIS_HOST_PORT%/0"
    set "CACHE_REDIS_URL=redis://:%REDIS_PASSWORD%@127.0.0.1:%REDIS_HOST_PORT%/1"
) else (
    set "CELERY_RESULT_BACKEND=redis://127.0.0.1:%REDIS_HOST_PORT%/0"
    set "CACHE_REDIS_URL=redis://127.0.0.1:%REDIS_HOST_PORT%/1"
)
set "VITE_API_BASE_URL=/api"
set "PYTHONUTF8=1"

set "DEV_COMPOSE_FILE=%ROOT%\docker-compose.yml"
set "DEV_COMPOSE_OVERRIDE=%ROOT%\docker-compose.override.yml.example"

if /I "%~1"=="check" (
    echo ROOT=%ROOT%
    echo ENV_FILE=%ENV_FILE%
    echo BACKEND=http://127.0.0.1:%BACKEND_API_PORT%
    echo FRONTEND=http://127.0.0.1:%FRONTEND_HTTP_PORT%
    echo POSTGRES_HOST_PORT=%POSTGRES_HOST_PORT%
    echo RABBITMQ_HOST_PORT=%RABBITMQ_HOST_PORT%
    echo REDIS_HOST_PORT=%REDIS_HOST_PORT%
    echo MINIO_API_HOST_PORT=%MINIO_API_HOST_PORT%
    echo DATABASE_URL=%DATABASE_URL%
    echo CELERY_BROKER_URL=%CELERY_BROKER_URL%
    echo CACHE_REDIS_URL=%CACHE_REDIS_URL%
    echo MINIO_ENDPOINT=%MINIO_ENDPOINT%
)

exit /b 0
