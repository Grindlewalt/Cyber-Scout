#!/bin/bash
# The training environment for live/train/cstrain.py, all on the NAS disk (the root disk is never used):
#   bash live/train/setup_env.sh [ENV_DIR]       (default /mnt/nas/autoscout-scratch/trainenv; safe to re-run)
# Python 3.12 (uv), ultralytics 8.4.138 + CUDA torch, onnx / onnxslim, onnxruntime-gpu (the worker's 1.31.0, for
# the ONNX eval path), av, opencv-python-headless.  Base weights live in $TRAIN_DIR/weights.
set -eu
ENV=${1:-/mnt/nas/autoscout-scratch/trainenv}
SCRATCH=${SCRATCH:-/mnt/nas/autoscout-scratch}
TRAIN_DIR=${TRAIN_DIR:-$SCRATCH/train}
TORCH_INDEX=${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}
export UV_CACHE_DIR=${UV_CACHE_DIR:-$SCRATCH/.uv-cache}
export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$SCRATCH/.uv-python}

if ! command -v uv >/dev/null 2>&1; then
  [ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  export PATH="$HOME/.local/bin:$PATH"
fi
[ -x "$ENV/bin/python" ] || uv venv -p 3.12 "$ENV"
PY="$ENV/bin/python"

# torch first, from the CUDA index (the driver must support its CUDA: nvidia-smi "CUDA Version")
"$PY" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null \
  || uv pip install -p "$PY" --index-url "$TORCH_INDEX" torch torchvision
uv pip install -p "$PY" ultralytics==8.4.138 onnx onnxslim av opencv-python-headless pyyaml
# onnxruntime (CPU) and onnxruntime-gpu are the same module: keep only the GPU one
if "$PY" -c "import importlib.metadata as m; m.version('onnxruntime')" >/dev/null 2>&1; then
  uv pip uninstall -p "$PY" onnxruntime
fi
uv pip install -p "$PY" "onnxruntime-gpu[cuda,cudnn]==1.31.0"
# ultralytics pulls opencv-python (GUI build) too; the headless one must win
uv pip install -p "$PY" --reinstall opencv-python-headless >/dev/null

mkdir -p "$TRAIN_DIR/weights" "$TRAIN_DIR/.ultralytics"
for w in yolo11s.pt yolo11m.pt; do
  [ -f "$TRAIN_DIR/weights/$w" ] || [ ! -f "$SCRATCH/$w" ] || cp "$SCRATCH/$w" "$TRAIN_DIR/weights/"
done

"$PY" - <<'EOF'
import torch, ultralytics, onnxruntime as ort, cv2, av
print("torch", torch.__version__, "CUDA", torch.version.cuda, "GPUs",
      [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])
print("ultralytics", ultralytics.__version__, "| onnxruntime", ort.__version__, ort.get_available_providers()[:2],
      "| opencv", cv2.__version__, "| av", av.__version__)
EOF
echo "ready: TRAIN_DIR=$TRAIN_DIR $PY live/train/cstrain.py status --project <name>"
