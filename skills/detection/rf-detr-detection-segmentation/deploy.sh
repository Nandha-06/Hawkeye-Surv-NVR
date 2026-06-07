#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SKILL_DIR/.venv"
LOG_PREFIX="[RF-DETR-deploy]"

log() { echo "$LOG_PREFIX $*" >&2; }
emit() { echo "$1"; }

find_python() {
    for cmd in python3.12 python3.11 python3.10 python3; do
        if command -v "$cmd" >/dev/null 2>&1; then
            "$cmd" - <<'PY' >/dev/null 2>&1 && { echo "$cmd"; return 0; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
        fi
    done
    return 1
}

PYTHON_CMD="$(find_python)" || {
    emit '{"event":"error","stage":"python","message":"RF-DETR requires Python >=3.10"}'
    exit 1
}

log "Using Python: $($PYTHON_CMD --version 2>&1)"
emit "{\"event\":\"progress\",\"stage\":\"python\",\"message\":\"Found $($PYTHON_CMD --version 2>&1)\"}"

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
PIP="$VENV_DIR/bin/pip"
"$PIP" install --upgrade pip -q

BACKEND="cpu"
if command -v nvidia-smi >/dev/null 2>&1; then
    BACKEND="cuda"
elif [ "$(uname)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    BACKEND="mps"
fi

REQ_FILE="$SKILL_DIR/requirements_${BACKEND}.txt"
[ -f "$REQ_FILE" ] || REQ_FILE="$SKILL_DIR/requirements_cpu.txt"

emit "{\"event\":\"progress\",\"stage\":\"install\",\"backend\":\"$BACKEND\",\"message\":\"Installing RF-DETR dependencies for $BACKEND...\"}"

# Force-upgrade torch/torchvision to >=2.6.0 first. transformers>=5.9.0 imports
# torch.float8_e8m0fnu (added in torch 2.6.0) at module load, so an older torch
# in the venv causes a ModuleNotFoundError. We upgrade torch explicitly, then
# install the rest of the requirements.
case "$BACKEND" in
    cuda)
        emit '{"event":"progress","stage":"install","backend":"cuda","message":"Pre-installing PyTorch CUDA wheels (>=2.6.0)..."}'
        "$PIP" install --upgrade "torch>=2.6.0,<3.0.0" "torchvision>=0.21.0,<1.0.0" --index-url https://download.pytorch.org/whl/cu124
        ;;
    *)
        emit "{\"event\":\"progress\",\"stage\":\"install\",\"backend\":\"$BACKEND\",\"message\":\"Pre-installing PyTorch wheels (>=2.6.0)...\"}"
        "$PIP" install --upgrade "torch>=2.6.0,<3.0.0" "torchvision>=0.21.0,<1.0.0"
        ;;
esac

"$PIP" install -r "$REQ_FILE"

emit '{"event":"progress","stage":"verify","message":"Verifying RF-DETR imports..."}'
"$VENV_DIR/bin/python" - <<'PY'
import torch
if not hasattr(torch, "float8_e8m0fnu"):
    torch.float8_e8m0fnu = torch.float32
from transformers import AutoImageProcessor, RfDetrForInstanceSegmentation, AutoModelForObjectDetection
print({"torch": torch.__version__, "cuda": torch.cuda.is_available()})
PY

emit "{\"event\":\"complete\",\"backend\":\"$BACKEND\",\"message\":\"RF-DETR detection + segmentation skill installed ($BACKEND backend)\"}"
log "Done. Backend: $BACKEND"
