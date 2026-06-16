@echo off
setlocal EnableExtensions
title Knowledge Uploader Docker Check

call "%~dp0load-dev-env.bat"
if errorlevel 1 exit /b 1

if /I "%~1"=="check" (
    echo This script runs the full Docker-oriented verification when called without check.
    echo Commands: python -m invoke up, migrate, lint, test
    exit /b 0
)

cd /d "%ROOT%"
python -m invoke up
if errorlevel 1 exit /b 1

python -m invoke migrate
if errorlevel 1 exit /b 1

python -m invoke lint
if errorlevel 1 exit /b 1

python -m invoke test
exit /b %errorlevel%
