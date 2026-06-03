#!/bin/bash
# deploy.sh — Zero-assumption bootstrapper for Moondream Live Video skill (Linux/macOS)

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SKILL_DIR/.venv"
LOG_PREFIX="[Moondream-Video-deploy]"

echo "$LOG_PREFIX Searching for Python..." >&2

PYTHON_CMD=""
for cmd in python3 python; do
    if command -v $cmd >/dev/null 2>&1; then
        # Check version >= 3.9
        ver=$($cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo $ver | cut -d. -f1)
        minor=$(echo $ver | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON_CMD=$cmd
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "$LOG_PREFIX ERROR: No Python >=3.9 found. Install Python 3.9+ and retry." >&2
    echo '{"event": "error", "stage": "python", "message": "No Python >=3.9 found"}'
    exit 1
fi

PY_VERSION=$($PYTHON_CMD --version 2>&1)
echo "$LOG_PREFIX Using Python: $PYTHON_CMD ($PY_VERSION)" >&2
echo "{\"event\": \"progress\", \"stage\": \"python\", \"message\": \"Found $PY_VERSION\"}"

# Create venv
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "$LOG_PREFIX Creating virtual environment..." >&2
    echo '{"event": "progress", "stage": "venv", "message": "Creating virtual environment..."}'
    $PYTHON_CMD -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "$LOG_PREFIX ERROR: Failed to create virtual environment" >&2
        echo '{"event": "error", "stage": "venv", "message": "Failed to create venv"}'
        exit 1
    fi
fi

PIP="$VENV_DIR/bin/pip"
VPYTHON="$VENV_DIR/bin/python"

$PIP install --upgrade pip -q >/dev/null 2>&1
echo '{"event": "progress", "stage": "venv", "message": "Virtual environment ready"}'

# Install requirements
echo "$LOG_PREFIX Installing dependencies from requirements.txt..." >&2
echo '{"event": "progress", "stage": "install", "message": "Installing python packages..."}'

$PIP install -r "$SKILL_DIR/requirements.txt" -q 2>&1
if [ $? -ne 0 ]; then
    echo "$LOG_PREFIX ERROR: Package installation failed" >&2
    echo '{"event": "error", "stage": "install", "message": "Dependency installation failed"}'
    exit 1
fi

echo '{"event": "complete", "backend": "cpu", "message": "Moondream Live Video skill installed successfully!"}'
echo "$LOG_PREFIX Done!" >&2
exit 0
