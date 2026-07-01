@echo off
title MFGFLOW Dashboard
echo Starting MFGFLOW Dashboard...
echo.

:: Try 'python' first, then 'python3'
where python >nul 2>&1
if %errorlevel% == 0 (
    python "%~dp0start_dashboard.py"
    goto end
)

where python3 >nul 2>&1
if %errorlevel% == 0 (
    python3 "%~dp0start_dashboard.py"
    goto end
)

:: Python not found
echo Python is not installed on this computer.
echo.
echo Please download and install it from: https://www.python.org/downloads/
echo Make sure to tick "Add Python to PATH" during installation.
echo.
:end
pause
