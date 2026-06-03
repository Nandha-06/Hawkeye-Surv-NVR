#!/usr/bin/env bash
# deploy.sh — Bootstrapper for Home Assistant Bridge Skill (Linux/macOS)

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SKILL_DIR/.venv"
LOG_PREFIX="[HASS-Bridge-deploy]"

log()  { echo "$LOG_PREFIX $*" >&2; }
emit() { echo "$1"; }

find_python() {
    for cmd in python3.12 python3.11 python3.10 python3.9 python3; do
        if command -v "$cmd" &>/dev/null; then
            echo "$cmd"
            return 0
        fi
    done
    return 1
}

PYTHON_CMD=$(find_python) || {
    log "ERROR: No Python >=3.9 found."
    emit '{"event": "error", "stage": "python", "message": "No Python >=3.9 found"}'
    exit 1
}

log "Using Python: $PYTHON_CMD ($($PYTHON_CMD --version 2>&1))"
emit "{\"event\": \"progress\", \"stage\": \"python\", \"message\": \"Found $($PYTHON_CMD --version 2>&1)\"}"

if [ ! -d "$VENV_DIR" ]; then
    log "Creating virtual environment..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
PIP="$VENV_DIR/bin/pip"
"$PIP" install --upgrade pip -q || true

emit '{"event": "progress", "stage": "venv", "message": "Virtual environment ready"}'

log "Installing dependencies from requirements.txt..."
emit '{"event": "progress", "stage": "install", "message": "Installing Python packages..."}'

if "$PIP" install -r "$SKILL_DIR/requirements.txt" -q; then
    emit '{"event": "complete", "message": "Home Assistant Bridge skill installed successfully"}'
    log "Done!"
    exit 0
else
    log "ERROR: Dependency installation failed"
    emit '{"event": "error", "stage": "install", "message": "Dependency installation failed"}'
    exit 1
fi
