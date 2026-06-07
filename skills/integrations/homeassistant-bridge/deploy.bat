@echo off
REM deploy.bat — Bootstrapper for Home Assistant Bridge Skill (Windows)

setlocal enabledelayedexpansion

set "SKILL_DIR=%~dp0"
if "%SKILL_DIR:~-1%"=="\" set "SKILL_DIR=%SKILL_DIR:~0,-1%"
set "VENV_DIR=%SKILL_DIR%\.venv"
set "LOG_PREFIX=[HASS-Bridge-deploy]"

echo %LOG_PREFIX% Searching for Python...>&2
set "PYTHON_CMD="

for %%V in (3.12 3.11 3.10 3.9) do (
    if not defined PYTHON_CMD (
        py -%%V --version >nul 2>&1
        if !errorlevel! equ 0 set "PYTHON_CMD=py -%%V"
    )
)

if not defined PYTHON_CMD (
    python3 --version >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON_CMD=python3"
)

if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo %LOG_PREFIX% ERROR: No Python >=3.9 found.>&2
    echo {"event": "error", "stage": "python", "message": "No Python >=3.9 found"}
    exit /b 1
)

for /f "tokens=*" %%A in ('!PYTHON_CMD! --version 2^>^&1') do set "PY_VERSION=%%A"
echo %LOG_PREFIX% Using Python: %PYTHON_CMD% (%PY_VERSION%)>&2
echo {"event": "progress", "stage": "python", "message": "Found %PY_VERSION%"}

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo %LOG_PREFIX% Creating virtual environment...>&2
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 (
        echo %LOG_PREFIX% ERROR: Failed to create virtual environment>&2
        echo {"event": "error", "stage": "venv", "message": "Failed to create venv"}
        exit /b 1
    )
)

set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "VPYTHON=%VENV_DIR%\Scripts\python.exe"

"%VPYTHON%" -m pip --version >nul 2>&1
if !errorlevel! neq 0 (
    echo {"event":"progress","stage":"venv","message":"Bootstrapping pip..."}
    "%VPYTHON%" -m ensurepip --upgrade >nul 2>&1
)

"%PIP%" install --upgrade pip -q >nul 2>&1
echo {"event": "progress", "stage": "venv", "message": "Virtual environment ready"}

echo %LOG_PREFIX% Installing dependencies from requirements.txt...>&2
echo {"event": "progress", "stage": "install", "message": "Installing Python packages..."}
"%PIP%" install -r "%SKILL_DIR%\requirements.txt" -q

if !errorlevel! equ 0 (
    echo {"event": "complete", "message": "Home Assistant Bridge skill installed successfully"}
    echo %LOG_PREFIX% Done!>&2
    exit /b 0
) else (
    echo %LOG_PREFIX% ERROR: Dependency installation failed>&2
    echo {"event": "error", "stage": "install", "message": "Dependency installation failed"}
    exit /b 1
)
