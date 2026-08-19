#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "MuseTalk setup failed near line $LINENO." >&2' ERR

log() {
    echo
    echo "==> $*"
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

BASE_ROOT="${1:-$HOME/avatar-engine}"
MINIFORGE_ROOT="$BASE_ROOT/miniforge3"
MUSETALK_ROOT="$BASE_ROOT/MuseTalk"
CONDA="$MINIFORGE_ROOT/bin/conda"
ENV_ROOT="$MINIFORGE_ROOT/envs/musetalk"
PYTHON="$ENV_ROOT/bin/python"
PIP="$ENV_ROOT/bin/pip"
MIM="$ENV_ROOT/bin/mim"
GDOWN="$ENV_ROOT/bin/gdown"

MUSETALK_REPO="https://github.com/TMElyralab/MuseTalk.git"
MUSETALK_COMMIT="0a89dec45a0192b824e3cf4daf96c239440c5ed8"

SCRIPT_ROOT="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
    pwd
)"
PATCH_FILE="$SCRIPT_ROOT/musetalk-direct-video.patch"

[[ "$(uname -m)" == "x86_64" ]] || \
    fail "MuseTalk installer currently supports x86_64 Linux only."

command -v git >/dev/null 2>&1 || fail "git is required."
command -v curl >/dev/null 2>&1 || fail "curl is required."
command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is required."
command -v nvidia-smi >/dev/null 2>&1 || fail "NVIDIA GPU is required."

nvidia-smi >/dev/null 2>&1 || \
    fail "NVIDIA driver is installed but the GPU is not available."

mkdir -p "$BASE_ROOT"

if [[ ! -x "$CONDA" ]]; then
    log "Installing Miniforge"

    installer="$BASE_ROOT/Miniforge3-Linux-x86_64.sh"

    curl -fL \
        "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh" \
        -o "$installer"

    bash "$installer" \
        -b \
        -p "$MINIFORGE_ROOT"

    rm -f "$installer"
else
    log "Existing Miniforge preserved"
fi

if [[ ! -d "$MUSETALK_ROOT/.git" ]]; then
    log "Cloning pinned MuseTalk"

    git clone "$MUSETALK_REPO" "$MUSETALK_ROOT"
fi

cd "$MUSETALK_ROOT"

CURRENT_REMOTE="$(
    git remote get-url origin 2>/dev/null || true
)"

[[ "$CURRENT_REMOTE" == "$MUSETALK_REPO" ]] || \
    fail "Existing MuseTalk directory has an unexpected git remote."

CURRENT_COMMIT="$(git rev-parse HEAD)"

if [[ "$CURRENT_COMMIT" != "$MUSETALK_COMMIT" ]]; then
    log "Checking out pinned MuseTalk commit"

    if ! git diff --quiet || ! git diff --cached --quiet; then
        fail "MuseTalk has local tracked changes at an unexpected commit."
    fi

    git fetch --depth 1 origin "$MUSETALK_COMMIT"
    git checkout --detach "$MUSETALK_COMMIT"
fi

[[ -f "$PATCH_FILE" ]] || \
    fail "MuseTalk direct-video patch is missing: $PATCH_FILE"

if git apply --reverse --check "$PATCH_FILE" >/dev/null 2>&1; then
    log "MuseTalk direct-video patch already applied"
elif git apply --check "$PATCH_FILE" >/dev/null 2>&1; then
    log "Applying MuseTalk direct-video optimization"
    git apply "$PATCH_FILE"
else
    fail "MuseTalk direct-video patch does not apply cleanly."
fi

environment_ready() {
    [[ -x "$PYTHON" ]] || return 1

    "$PYTHON" - <<'PY' >/dev/null 2>&1
import importlib.metadata as md
import torch

expected = {
    "torch": "2.0.1",
    "torchvision": "0.15.2",
    "torchaudio": "2.0.2",
    "accelerate": "0.28.0",
    "diffusers": "0.30.2",
    "transformers": "4.39.2",
    "numpy": "1.23.5",
    "omegaconf": "2.3.1",
    "librosa": "0.11.0",
    "soundfile": "0.12.1",
    "huggingface-hub": "0.30.2",
    "mmcv": "2.0.1",
    "mmengine": "0.10.7",
    "mmdet": "3.1.0",
    "mmpose": "1.1.0",
    "chumpy": "0.70",
    "openmim": "0.3.9",
}

for package, version in expected.items():
    actual = md.version(package).split("+", 1)[0]
    if actual != version:
        raise SystemExit(
            f"{package}: expected {version}, found {actual}"
        )

if not torch.cuda.is_available():
    raise SystemExit("CUDA unavailable")

if torch.version.cuda != "11.8":
    raise SystemExit(
        f"Expected CUDA runtime 11.8, found {torch.version.cuda}"
    )
PY
}

if environment_ready; then
    log "Existing pinned MuseTalk environment preserved"
else
    log "Creating pinned MuseTalk Python environment"

    "$CONDA" env remove -n musetalk -y >/dev/null 2>&1 || true

    "$CONDA" create \
        -y \
        -n musetalk \
        python=3.10

    PYTHON="$MINIFORGE_ROOT/envs/musetalk/bin/python"
    PIP="$MINIFORGE_ROOT/envs/musetalk/bin/pip"
    MIM="$MINIFORGE_ROOT/envs/musetalk/bin/mim"
    GDOWN="$MINIFORGE_ROOT/envs/musetalk/bin/gdown"

    "$PIP" install \
        --upgrade \
        pip \
        setuptools \
        wheel

    log "Installing CUDA 11.8 PyTorch runtime"

    "$PIP" install \
        torch==2.0.1 \
        torchvision==0.15.2 \
        torchaudio==2.0.2 \
        --index-url \
        https://download.pytorch.org/whl/cu118

    log "Installing pinned MuseTalk requirements"

    "$PIP" install \
        diffusers==0.30.2 \
        accelerate==0.28.0 \
        numpy==1.23.5 \
        tensorflow==2.12.0 \
        tensorboard==2.12.0 \
        opencv-python==4.9.0.80 \
        soundfile==0.12.1 \
        transformers==4.39.2 \
        huggingface_hub==0.30.2 \
        librosa==0.11.0 \
        einops==0.8.1 \
        gradio==5.24.0 \
        gdown==6.1.0 \
        requests \
        'imageio[ffmpeg]' \
        omegaconf==2.3.1 \
        ffmpeg-python \
        moviepy

    log "Installing pinned OpenMMLab runtime"

    "$PIP" install openmim==0.3.9

    "$PIP" install \
        --no-build-isolation \
        chumpy==0.70

    "$MIM" install mmengine==0.10.7
    "$MIM" install mmcv==2.0.1
    "$MIM" install mmdet==3.1.0
    "$MIM" install mmpose==1.1.0

    # Preserve the known-working versions after OpenMIM resolves
    # its own utility dependencies.
    "$PIP" install \
        rich==13.4.2

    "$PIP" install \
        --no-deps \
        typer==0.27.1

    environment_ready || \
        fail "Pinned MuseTalk environment verification failed."
fi

cd "$MUSETALK_ROOT"

mkdir -p \
    models/musetalkV15 \
    models/sd-vae \
    models/whisper \
    models/dwpose \
    models/syncnet \
    models/face-parse-bisent

download_hf_files() {
    local repo_id="$1"
    local target="$2"
    shift 2

    HF_REPO_ID="$repo_id" \
    HF_TARGET="$target" \
    HF_FILES="$*" \
    HF_HUB_DISABLE_XET=1 \
    "$PYTHON" - <<'PY'
import os
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id=os.environ["HF_REPO_ID"],
    local_dir=os.environ["HF_TARGET"],
    allow_patterns=os.environ["HF_FILES"].split(),
)
PY
}

file_large_enough() {
    local file="$1"
    local minimum="$2"
    local bytes

    bytes="$(stat -c%s "$file" 2>/dev/null || echo 0)"
    (( bytes >= minimum ))
}

if ! file_large_enough \
    models/musetalkV15/unet.pth \
    3000000000
then
    log "Downloading MuseTalk v1.5 model"

    download_hf_files \
        TMElyralab/MuseTalk \
        models \
        musetalkV15/musetalk.json \
        musetalkV15/unet.pth
else
    log "Existing MuseTalk v1.5 model preserved"
fi

if ! file_large_enough \
    models/sd-vae/diffusion_pytorch_model.bin \
    250000000
then
    log "Downloading SD VAE"

    download_hf_files \
        stabilityai/sd-vae-ft-mse \
        models/sd-vae \
        config.json \
        diffusion_pytorch_model.bin
else
    log "Existing SD VAE preserved"
fi

if ! file_large_enough \
    models/whisper/pytorch_model.bin \
    100000000
then
    log "Downloading MuseTalk Whisper model"

    download_hf_files \
        openai/whisper-tiny \
        models/whisper \
        config.json \
        pytorch_model.bin \
        preprocessor_config.json
else
    log "Existing MuseTalk Whisper model preserved"
fi

if ! file_large_enough \
    models/dwpose/dw-ll_ucoco_384.pth \
    300000000
then
    log "Downloading DWPose model"

    download_hf_files \
        yzd-v/DWPose \
        models/dwpose \
        dw-ll_ucoco_384.pth
else
    log "Existing DWPose model preserved"
fi

if ! file_large_enough \
    models/syncnet/latentsync_syncnet.pt \
    1000000000
then
    log "Downloading SyncNet model"

    download_hf_files \
        ByteDance/LatentSync \
        models/syncnet \
        latentsync_syncnet.pt
else
    log "Existing SyncNet model preserved"
fi

if ! file_large_enough \
    models/face-parse-bisent/79999_iter.pth \
    40000000
then
    log "Downloading face parsing model"

    "$GDOWN" \
        154JgKpzCPW82qINcVieuPH3fZ2e0P812 \
        -O models/face-parse-bisent/79999_iter.pth
else
    log "Existing face parsing model preserved"
fi

if ! file_large_enough \
    models/face-parse-bisent/resnet18-5c106cde.pth \
    40000000
then
    log "Downloading face parsing ResNet"

    curl -fL \
        https://download.pytorch.org/models/resnet18-5c106cde.pth \
        -o models/face-parse-bisent/resnet18-5c106cde.pth
else
    log "Existing face parsing ResNet preserved"
fi

log "Verifying MuseTalk model files"

file_large_enough models/musetalkV15/unet.pth 3000000000 || \
    fail "MuseTalk v1.5 UNet is incomplete."

[[ -s models/musetalkV15/musetalk.json ]] || \
    fail "MuseTalk v1.5 config is missing."

file_large_enough models/sd-vae/diffusion_pytorch_model.bin 250000000 || \
    fail "SD VAE is incomplete."

file_large_enough models/whisper/pytorch_model.bin 100000000 || \
    fail "MuseTalk Whisper model is incomplete."

file_large_enough models/dwpose/dw-ll_ucoco_384.pth 300000000 || \
    fail "DWPose model is incomplete."

file_large_enough models/syncnet/latentsync_syncnet.pt 1000000000 || \
    fail "SyncNet model is incomplete."

file_large_enough models/face-parse-bisent/79999_iter.pth 40000000 || \
    fail "Face parsing model is incomplete."

file_large_enough models/face-parse-bisent/resnet18-5c106cde.pth 40000000 || \
    fail "Face parsing ResNet is incomplete."

log "Running MuseTalk import/GPU verification"

cd "$MUSETALK_ROOT"

PYTORCH_CUDA_ALLOC_CONF= \
"$PYTHON" - <<'PY'
import cv2
import torch
import diffusers
import transformers
import mmcv
import mmengine
import mmdet
import mmpose

if not torch.cuda.is_available():
    raise SystemExit("MuseTalk CUDA unavailable.")

print("MUSETALK_SETUP_OK")
print("Torch:", torch.__version__)
print("CUDA runtime:", torch.version.cuda)
print("GPU:", torch.cuda.get_device_name(0))
PY

echo
echo "MUSETALK_INSTALLATION_COMPLETE"
echo "Root: $MUSETALK_ROOT"
echo "Environment: $ENV_ROOT"
echo "Commit: $MUSETALK_COMMIT"
