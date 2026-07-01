@echo off
setlocal EnableExtensions
title Knowledge Uploader Web

call "%~dp0load-dev-env.bat"
if errorlevel 1 exit /b 1

where.exe npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm was not found. Install Node.js 20.19+ and reopen this terminal.
    exit /b 1
)

if /I "%~1"=="check" (
    if not exist "%ROOT%\frontend\package.json" (
        echo [ERROR] Missing frontend\package.json.
        exit /b 1
    )
    echo npm command is available.
    echo Web check completed.
    exit /b 0
)

if /I "%~1"=="deps" (
    if not exist "%ROOT%\frontend\node_modules\.bin\vite.cmd" (
        echo [ERROR] Frontend dependencies are missing.
        echo         Run scripts\dev-setup.bat first, or run npm install in frontend.
        exit /b 1
    )
    echo Frontend development dependencies are available.
    exit /b 0
)

echo ============================================================
echo  Knowledge Uploader Web
echo  Web:       http://127.0.0.1:%FRONTEND_HTTP_PORT%
echo  API proxy: /api -^> http://127.0.0.1:%BACKEND_API_PORT%
echo ============================================================
echo.

cd /d "%ROOT%\frontend"
npm run dev -- --host 127.0.0.1 --port %FRONTEND_HTTP_PORT%
