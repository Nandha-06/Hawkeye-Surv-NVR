@echo off
REM deploy.bat — Zero-assumption bootstrapper for Moondream Live Video skill (Windows)
REM Probes the system for Python, creates a virtual environment, and installs requirements.

setlocal enabledelayedexpansion

set "SKILL_DIR=%~dp0"
if "%SKILL_DIR:~-1%"=="\" set "SKILL_DIR=%SKILL_DIR:~0,-1%"
set "VENV_DIR=%SKILL_DIR%\.venv"
set "LOG_PREFIX=[Moondream-Video-deploy]"

echo %LOG_PREFIX% Searching for Python...>&2

set "PYTHON_CMD="

REM Try py launcher
for %%V in (3.12 3.11 3.10 3.9) do (
    if not defined PYTHON_CMD (
        py -%%V --version >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_CMD=py -%%V"
        )
    )
)

REM Fallback to bare python3
if not defined PYTHON_CMD (
    python3 --version >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=2 delims= " %%A in ('python3 --version 2^>^&1') do set "_pyver=%%A"
        for /f "tokens=1,2 delims=." %%A in ("!_pyver!") do (
            if %%A geq 3 if %%B geq 9 set "PYTHON_CMD=python3"
        )
    )
)

REM Fallback to bare python
if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=2 delims= " %%A in ('python --version 2^>^&1') do set "_pyver=%%A"
        for /f "tokens=1,2 delims=." %%A in ("!_pyver!") do (
            if %%A geq 3 if %%B geq 9 set "PYTHON_CMD=python"
        )
    )
)

if not defined PYTHON_CMD (
    echo %LOG_PREFIX% ERROR: No Python ^>=3.9 found. Install Python 3.9+ and retry.>&2
    echo {"event": "error", "stage": "python", "message": "No Python >=3.9 found"}
    exit /b 1
)

for /f "tokens=*" %%A in ('!PYTHON_CMD! --version 2^>^&1') do set "PY_VERSION=%%A"
echo %LOG_PREFIX% Using Python: %PYTHON_CMD% (%PY_VERSION%)>&2
echo {"event": "progress", "stage": "python", "message": "Found %PY_VERSION%"}

REM Create venv
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo %LOG_PREFIX% Creating virtual environment...>&2
    echo {"event": "progress", "stage": "venv", "message": "Creating virtual environment..."}
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 (
        echo %LOG_PREFIX% ERROR: Failed to create virtual environment>&2
        echo {"event": "error", "stage": "venv", "message": "Failed to create venv"}
        exit /b 1
    )
)

set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "VPYTHON=%VENV_DIR%\Scripts\python.exe"

"%PIP%" install --upgrade pip -q >nul 2>&1

echo {"event": "progress", "stage": "venv", "message": "Virtual environment ready"}

REM Install requirements
echo %LOG_PREFIX% Installing dependencies from requirements.txt...>&2
echo {"event": "progress", "stage": "install", "message": "Installing python packages..."}

"%PIP%" install -r "%SKILL_DIR%\requirements.txt" -q 2>&1
if !errorlevel! neq 0 (
    echo %LOG_PREFIX% ERROR: Package installation failed>&2
    echo {"event": "error", "stage": "install", "message": "Dependency installation failed"}
    exit /b 1
)

echo {"event": "complete", "backend": "cpu", "message": "Moondream Live Video skill installed successfully!"}
echo %LOG_PREFIX% Done!>&2

endlocal
exit /b 0
