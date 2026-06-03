@echo off
setlocal enabledelayedexpansion

set "SKILL_DIR=%~dp0"
if "%SKILL_DIR:~-1%"=="\" set "SKILL_DIR=%SKILL_DIR:~0,-1%"
set "VENV_DIR=%SKILL_DIR%\.venv"
set "LOG_PREFIX=[RF-DETR-deploy]"

echo %LOG_PREFIX% Searching for Python...>&2
set "PYTHON_CMD="
for %%V in (3.12 3.11 3.10) do (
    if not defined PYTHON_CMD (
        py -%%V --version >nul 2>&1
        if !errorlevel! equ 0 set "PYTHON_CMD=py -%%V"
    )
)
if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo {"event":"error","stage":"python","message":"RF-DETR requires Python >=3.10"}
    exit /b 1
)

for /f "tokens=*" %%A in ('!PYTHON_CMD! --version 2^>^&1') do set "PY_VERSION=%%A"
echo {"event":"progress","stage":"python","message":"Found !PY_VERSION!"}

if not exist "%VENV_DIR%\Scripts\python.exe" (
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 (
        echo {"event":"error","stage":"venv","message":"Failed to create virtual environment"}
        exit /b 1
    )
)

set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "VPYTHON=%VENV_DIR%\Scripts\python.exe"
"%PIP%" install --upgrade pip -q

set "BACKEND=cpu"
where nvidia-smi >nul 2>&1
if !errorlevel! equ 0 set "BACKEND=cuda"

set "REQ_FILE=%SKILL_DIR%\requirements_!BACKEND!.txt"
if not exist "!REQ_FILE!" set "REQ_FILE=%SKILL_DIR%\requirements_cpu.txt"

echo {"event":"progress","stage":"install","backend":"!BACKEND!","message":"Installing RF-DETR dependencies for !BACKEND!..."}
"%PIP%" install -r "!REQ_FILE!"
if !errorlevel! neq 0 (
    echo {"event":"error","stage":"install","message":"Dependency installation failed"}
    exit /b 1
)

echo {"event":"progress","stage":"verify","message":"Verifying RF-DETR imports..."}
"%VPYTHON%" -c "from transformers import AutoImageProcessor, RfDetrForInstanceSegmentation; import torch; print({'torch': torch.__version__, 'cuda': torch.cuda.is_available()})"
if !errorlevel! neq 0 (
    echo {"event":"error","stage":"verify","message":"RF-DETR import verification failed"}
    exit /b 1
)

echo {"event":"complete","backend":"!BACKEND!","message":"RF-DETR detection + segmentation skill installed (!BACKEND! backend)"}
echo %LOG_PREFIX% Done. Backend: !BACKEND!>&2
endlocal
exit /b 0
