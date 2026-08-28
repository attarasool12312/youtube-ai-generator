#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "Installation failed near line $LINENO." >&2' ERR

log() {
    echo
    echo "==> $*"
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

if [[ "$EUID" -ne 0 ]]; then
    exec sudo -E bash "$0" "$@"
fi

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_OWNER="$(stat -c '%U' "$PROJECT_ROOT")"

if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    APP_USER="$SUDO_USER"
elif [[ "$PROJECT_OWNER" != "root" ]]; then
    APP_USER="$PROJECT_OWNER"
elif id ubuntu >/dev/null 2>&1; then
    APP_USER="ubuntu"
else
    APP_USER="root"
fi

APP_GROUP="$(id -gn "$APP_USER")"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"

MODEL_ROOT="${YOUTUBE_AI_MODEL_ROOT:-$(dirname "$PROJECT_ROOT")/models}"
VENV="$PROJECT_ROOT/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

SERVICE_NAME="youtube-ai-generator"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
OLLAMA_MODEL="qwen3:14b"

RECOVERY_ARCHIVE="$PROJECT_ROOT/recovery/runtime-state.tar.gz"
RECOVERY_CHECKSUM="$PROJECT_ROOT/recovery/runtime-state.sha256"

GENERATED_ADMIN_PASSWORD=""
RECOVERY_RESTORED="false"

run_as_user() {
    if [[ "$APP_USER" == "root" ]]; then
        "$@"
    else
        sudo -u "$APP_USER" -H "$@"
    fi
}


install_matching_nvenc_runtime() {
    if ldconfig -p 2>/dev/null |
        grep -F "libnvidia-encode.so.1" >/dev/null
    then
        log "NVENC runtime already available"
        return
    fi

    local package_line=""
    local package_name=""
    local driver_version=""
    local driver_series=""
    local encode_package=""
    local decode_package=""
    local package=""

    package_line="$(
        dpkg-query -W \
            -f='${Package}|${Version}|${Status}\n' \
            'nvidia-utils-*' \
            2>/dev/null |
        awk -F'|' \
            '$3 == "install ok installed" {
                print $1 "|" $2
            }' |
        sort -V |
        tail -n 1 ||
        true
    )"

    if [[ -z "$package_line" ]]; then
        package_line="$(
            dpkg-query -W \
                -f='${Package}|${Version}|${Status}\n' \
                'libnvidia-compute-*' \
                2>/dev/null |
            awk -F'|' \
                '$3 == "install ok installed" {
                    print $1 "|" $2
                }' |
            sort -V |
            tail -n 1 ||
            true
        )"
    fi

    if [[ -z "$package_line" ]]; then
        echo \
            "WARNING: NVIDIA package branch could not be detected. CPU rendering fallback will remain available." \
            >&2
        return
    fi

    package_name="${package_line%%|*}"
    driver_version="${package_line#*|}"

    case "$package_name" in
        nvidia-utils-*)
            driver_series="${package_name#nvidia-utils-}"
            ;;
        libnvidia-compute-*)
            driver_series="${package_name#libnvidia-compute-}"
            ;;
        *)
            echo \
                "WARNING: Unsupported NVIDIA package identity: $package_name. CPU rendering fallback will remain available." \
                >&2
            return
            ;;
    esac

    encode_package="libnvidia-encode-${driver_series}"
    decode_package="libnvidia-decode-${driver_series}"

    for package in \
        "$encode_package" \
        "$decode_package"
    do
        if ! apt-cache madison "$package" |
            awk '{print $3}' |
            grep -Fx "$driver_version" >/dev/null
        then
            echo \
                "WARNING: Exact package $package=$driver_version is unavailable. Skipping NVENC installation to avoid a driver mismatch." \
                >&2
            return
        fi
    done

    log \
        "Installing NVENC runtime matching NVIDIA ${driver_series} driver"

    if ! DEBIAN_FRONTEND=noninteractive \
        apt-get install -y \
        "$decode_package=$driver_version" \
        "$encode_package=$driver_version"
    then
        echo \
            "WARNING: NVENC runtime installation failed. CPU rendering fallback will remain available." \
            >&2
        return
    fi

    ldconfig

    if ldconfig -p 2>/dev/null |
        grep -F "libnvidia-encode.so.1" >/dev/null
    then
        log "Matching NVENC runtime installed successfully"
    else
        echo \
            "WARNING: NVENC library is unavailable after installation. CPU rendering fallback will remain available." \
            >&2
    fi
}

restore_recovery_state() {
    if [[ ! -f "$RECOVERY_ARCHIVE" ]]; then
        log "Recovery archive not found; using fresh-install mode"
        return
    fi

    [[ -f "$RECOVERY_CHECKSUM" ]] || \
        fail "Recovery checksum file is missing."

    log "Verifying disaster-recovery archive"

    (
        cd "$PROJECT_ROOT"
        sha256sum -c recovery/runtime-state.sha256
    )

    local recovery_tmp
    recovery_tmp="$(mktemp -d)"

    tar -xzf "$RECOVERY_ARCHIVE" -C "$recovery_tmp"

    [[ -f "$recovery_tmp/.env" ]] || \
        fail "Recovery archive does not contain .env."

    [[ -f "$recovery_tmp/backend/data/app.db" ]] || \
        fail "Recovery archive does not contain the database."

    mkdir -p "$PROJECT_ROOT/backend/data"

    install -o "$APP_USER" -g "$APP_GROUP" -m 600 \
        "$recovery_tmp/.env" \
        "$PROJECT_ROOT/.env"

    install -o "$APP_USER" -g "$APP_GROUP" -m 600 \
        "$recovery_tmp/backend/data/app.db" \
        "$PROJECT_ROOT/backend/data/app.db"

    # Hugging Face tokens are intentionally not restored from recovery.
    # A fresh token is requested interactively on every installer run.

    rm -rf "$recovery_tmp"

    RECOVERY_RESTORED="true"
    log "Environment, database and saved credentials restored"
}

restore_recovery_state

log "Installing Ubuntu system dependencies"

apt-get update

DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    git \
    ffmpeg \
    espeak-ng \
    redis-server \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    libsndfile1 \
    libffi-dev \
    libssl-dev \
    pkg-config \
    sqlite3 \
    unzip

command -v nvidia-smi >/dev/null 2>&1 || \
    fail "NVIDIA driver or GPU was not detected."

log "GPU detected"

nvidia-smi \
    --query-gpu=name,memory.total,driver_version \
    --format=csv,noheader

install_matching_nvenc_runtime

SWAP_MIN_KB=24000000
SWAP_TOTAL_KB="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)"

if (( SWAP_TOTAL_KB >= SWAP_MIN_KB )); then
    log "Existing swap is sufficient; preserving it"
else
    log "Configuring 24 GB swap"

    if [[ -e /swapfile ]]; then
        fail "Existing /swapfile found but active swap is below 24 GB. Inspect it manually."
    fi

    if ! fallocate -l 24G /swapfile; then
        rm -f /swapfile
        dd if=/dev/zero of=/swapfile \
            bs=1M count=24576 status=progress
    fi

    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile

    grep -qE '^/swapfile[[:space:]]' /etc/fstab || \
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "Swap status"
swapon --show
free -h

log "Creating project directories"

mkdir -p \
    "$MODEL_ROOT/huggingface" \
    "$PROJECT_ROOT/backend/data" \
    "$PROJECT_ROOT/logs/youtube-publishing" \
    "$PROJECT_ROOT/logs/facebook-publishing" \
    "$PROJECT_ROOT/outputs/jobs" \
    "$PROJECT_ROOT/logs"

chown -R "$APP_USER:$APP_GROUP" \
    "$PROJECT_ROOT" \
    "$MODEL_ROOT"

log "Preparing Python virtual environment"

if [[ ! -x "$PYTHON" ]]; then
    run_as_user /usr/bin/python3 -m venv "$VENV"
fi

run_as_user "$PIP" install --upgrade pip setuptools wheel
# Install base requirements first.
# Critical dependency installs must NEVER be allowed to fail silently.
run_as_user "$PIP" install -r "$PROJECT_ROOT/backend/requirements.txt"

# Chatterbox voice cloning is intentionally pinned.
run_as_user "$PIP" install chatterbox-tts==0.1.7

# Chatterbox 0.1.7 requires these exact versions.
# Re-assert them after dependency resolution so future packages cannot
# leave the environment with an incompatible Torch/Diffusers stack.
run_as_user "$PIP" install     torch==2.6.0     torchaudio==2.6.0     diffusers==0.29.0     safetensors==0.5.3

# Patch perth watermarker for platforms without binary
log "Patching perth watermarker fallback"
PERTH_INIT=$(find "$PROJECT_ROOT/.venv" -path "*/perth/__init__.py" 2>/dev/null | head -1)
if [ -n "$PERTH_INIT" ] && ! grep -q "DummyWatermarker fallback" "$PERTH_INIT" 2>/dev/null; then
    cat >> "$PERTH_INIT" << 'PERTH_PATCH'

# DummyWatermarker fallback for platforms where the binary is unavailable
if PerthImplicitWatermarker is None:
    PerthImplicitWatermarker = DummyWatermarker
PERTH_PATCH
    log "Perth watermarker patched"
fi

log "Installing isolated Whisper runtime"

run_as_user "$PIP" install --no-deps \
    faster-whisper==1.2.1 \
    ctranslate2==4.8.1 \
    onnxruntime==1.28.0 \
    av==18.0.0 \
    flatbuffers==25.12.19

# Hugging Face token (needed for Whisper, MuseTalk, Kokoro, Chatterbox)
# Always request a fresh token. Never silently reuse a cached/recovered token.
HF_TOKEN_FILE="$APP_HOME/.cache/huggingface/token"

echo
echo "============================================================"
echo "HUGGING FACE TOKEN REQUIRED"
echo "============================================================"
echo "Atta Video AI needs a Hugging Face READ token to download"
echo "Whisper, MuseTalk and related voice/avatar model files."
echo
echo "How to get a FREE Hugging Face token:"
echo "  1. Open: https://huggingface.co/settings/tokens"
echo "  2. Sign in to Hugging Face (create a free account if needed)."
echo "  3. Click 'Create new token'."
echo "  4. Give it a name, for example: AttaVideoAI"
echo "  5. Choose READ access (or Fine-grained with read access)."
echo "  6. Create the token, copy it, then paste it below."
echo
echo "Your input is hidden while you paste it."
echo

mkdir -p "$APP_HOME/.cache/huggingface"
chown "$APP_USER:$APP_GROUP" "$APP_HOME/.cache/huggingface"
chmod 700 "$APP_HOME/.cache/huggingface"

# Never silently use an old cached token.
rm -f "$HF_TOKEN_FILE"

for attempt in 1 2 3; do
    read -r -s -p "Hugging Face Token: " HF_TOKEN_INPUT
    echo

    if [[ -z "$HF_TOKEN_INPUT" ]]; then
        echo "Token cannot be empty."
        continue
    fi

    printf "%s" "$HF_TOKEN_INPUT" > "$HF_TOKEN_FILE"
    chown "$APP_USER:$APP_GROUP" "$HF_TOKEN_FILE"
    chmod 600 "$HF_TOKEN_FILE"
    unset HF_TOKEN_INPUT

    if run_as_user env \
        HF_TOKEN_FILE="$HF_TOKEN_FILE" \
        HF_HUB_DISABLE_XET=1 \
        "$PYTHON" - <<'PYHFVERIFY'
from pathlib import Path
import os
from huggingface_hub import HfApi

token = Path(os.environ["HF_TOKEN_FILE"]).read_text().strip()
HfApi().whoami(token=token)
print("HUGGING_FACE_TOKEN_OK")
PYHFVERIFY
    then
        log "Fresh Hugging Face token verified successfully"
        break
    fi

    rm -f "$HF_TOKEN_FILE"
    echo "Token validation failed. Please paste a valid Hugging Face READ token."

    if [[ "$attempt" == "3" ]]; then
        fail "Hugging Face authentication failed after 3 attempts."
    fi
done



log "Preparing Whisper caption model"

WHISPER_MODEL_DIR="$MODEL_ROOT/whisper/small.en"

mkdir -p "$WHISPER_MODEL_DIR"
chown -R "$APP_USER:$APP_GROUP" "$MODEL_ROOT/whisper"

if [[ -d "$APP_HOME/.cache/huggingface" ]]; then
    chown -R "$APP_USER:$APP_GROUP" "$APP_HOME/.cache/huggingface"
    chmod 700 "$APP_HOME/.cache/huggingface"
fi

if [[ -f "$APP_HOME/.cache/huggingface/token" ]]; then
    chmod 600 "$APP_HOME/.cache/huggingface/token"
fi

WHISPER_MODEL_FILE="$WHISPER_MODEL_DIR/model.bin"
WHISPER_MODEL_MIN_BYTES=100000000
WHISPER_MODEL_BYTES="$(
    stat -c%s "$WHISPER_MODEL_FILE" 2>/dev/null || echo 0
)"

if (( WHISPER_MODEL_BYTES < WHISPER_MODEL_MIN_BYTES )); then
    run_as_user env \
        HF_TOKEN_PATH="$APP_HOME/.cache/huggingface/token" \
        HF_HUB_DISABLE_XET=1 \
        WHISPER_MODEL_DIR="$WHISPER_MODEL_DIR" \
        "$PYTHON" - <<'PYWHISPER'
import os

from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="Systran/faster-whisper-small.en",
    local_dir=os.environ["WHISPER_MODEL_DIR"],
)
PYWHISPER
else
    log "Existing Whisper model preserved"
fi

WHISPER_MODEL_BYTES="$(
    stat -c%s "$WHISPER_MODEL_FILE" 2>/dev/null || echo 0
)"

(( WHISPER_MODEL_BYTES >= WHISPER_MODEL_MIN_BYTES )) ||     fail "Whisper model download is incomplete."

run_as_user "$PIP" check

log "Generating voice previews"

run_as_user env \
    PYTHONPATH="$PROJECT_ROOT/backend" \
    YOUTUBE_AI_PROJECT_ROOT="$PROJECT_ROOT" \
    YOUTUBE_AI_MODEL_ROOT="$MODEL_ROOT" \
    "$PYTHON" "$PROJECT_ROOT/scripts/generate_voice_previews.py"

log "Verifying narrator voices and previews"

run_as_user env \
    PYTHONPATH="$PROJECT_ROOT/backend" \
    YOUTUBE_AI_PROJECT_ROOT="$PROJECT_ROOT" \
    "$PYTHON" - <<'PYVOICES'
from app.voice_registry import (
    APPROVED_NARRATOR_VOICES,
    ADDITIONAL_VOICES,
    voice_preview_path,
)

voices = list(APPROVED_NARRATOR_VOICES)
additional = list(ADDITIONAL_VOICES)

if len(voices) != 28:
    raise SystemExit(
        f"Expected 28 approved narrator voices, found {len(voices)}."
    )

if len(additional) != 5:
    raise SystemExit(
        f"Expected 5 additional voices, found {len(additional)}."
    )

missing = [
    voice.voice_id
    for voice in voices
    if not voice_preview_path(voice.voice_id).is_file()
    or voice_preview_path(voice.voice_id).stat().st_size <= 1000
]

if missing:
    raise SystemExit(
        "Missing/invalid voice previews: " + ", ".join(missing)
    )

print("VOICE_SETUP_OK: 28 narrator voices, 5 additional voices, 28 previews")
PYVOICES

log "Starting Redis"

systemctl enable --now redis-server

redis-cli ping | grep -q PONG || \
    fail "Redis did not start."

if ! command -v ollama >/dev/null 2>&1; then
    log "Installing Ollama"
    curl -fsSL https://ollama.com/install.sh | sh
fi

log "Starting Ollama"

systemctl enable --now ollama

for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        break
    fi

    sleep 2
done

curl -fsS http://127.0.0.1:11434/api/tags >/dev/null || \
    fail "Ollama did not start."

log "Downloading Ollama model: $OLLAMA_MODEL"

run_as_user ollama pull "$OLLAMA_MODEL"

ENV_FILE="$PROJECT_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    log "Creating secure fresh-install environment"

    GENERATED_ADMIN_PASSWORD="$(
        "$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(18))'
    )"

    SECRET_KEY="$(
        "$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))'
    )"

    FERNET_KEY="$(
        "$PYTHON" -c \
        'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
    )"

    export PROJECT_ROOT
    export GENERATED_ADMIN_PASSWORD
    export SECRET_KEY
    export FERNET_KEY
    export OLLAMA_MODEL

    "$PYTHON" - <<'PYENV'
import json
import os
from pathlib import Path

root = Path(os.environ["PROJECT_ROOT"])

values = {
    "APP_NAME": "YouTube AI Video Generator",
    "APP_URL": "http://127.0.0.1:8000",
    "SECRET_KEY": os.environ["SECRET_KEY"],
    "SETTINGS_ENCRYPTION_KEY": os.environ["FERNET_KEY"],
    "ADMIN_USERNAME": "admin",
    "ADMIN_EMAIL": "admin@localhost.local",
    "INITIAL_ADMIN_PASSWORD": os.environ["GENERATED_ADMIN_PASSWORD"],
    "SMTP_HOST": "",
    "SMTP_PORT": "587",
    "SMTP_USERNAME": "",
    "SMTP_PASSWORD": "",
    "SMTP_USE_TLS": "true",
    "POSTGRES_DB": "",
    "POSTGRES_USER": "",
    "POSTGRES_PASSWORD": "",
    "DATABASE_URL": f"sqlite:///{root}/backend/data/app.db",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "SESSION_DAYS": "7",
    "DEFAULT_DURATION_MINUTES": "10",
    "DEFAULT_LANGUAGE": "English",
    "DEFAULT_VOICE": "male",
    "OLLAMA_URL": "http://127.0.0.1:11434",
    "OLLAMA_MODEL": os.environ["OLLAMA_MODEL"],
}

content = "\n".join(
    f"{key}={json.dumps(value)}"
    for key, value in values.items()
)

(root / ".env").write_text(content + "\n")
PYENV

    chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
else
    log "Existing or recovered .env preserved"
fi

log "Updating portable database path"

PROJECT_ROOT="$PROJECT_ROOT" \
ENV_FILE="$ENV_FILE" \
"$PYTHON" - <<'PYDB'
import json
import os
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
root = Path(os.environ["PROJECT_ROOT"])
database_url = f"sqlite:///{root}/backend/data/app.db"

lines = path.read_text().splitlines()
updated = []
found = False

for line in lines:
    if line.startswith("DATABASE_URL="):
        updated.append(f"DATABASE_URL={json.dumps(database_url)}")
        found = True
    else:
        updated.append(line)

if not found:
    updated.append(f"DATABASE_URL={json.dumps(database_url)}")

path.write_text("\n".join(updated) + "\n")
PYDB

chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
chmod 600 "$ENV_FILE"

log "Initializing database and administrator"

run_as_user env \
    YOUTUBE_AI_PROJECT_ROOT="$PROJECT_ROOT" \
    YOUTUBE_AI_MODEL_ROOT="$MODEL_ROOT" \
    PYTHONPATH="$PROJECT_ROOT/backend" \
    "$PYTHON" -m app.init_db

log "Running voice database migration"

run_as_user env \
    YOUTUBE_AI_PROJECT_ROOT="$PROJECT_ROOT" \
    YOUTUBE_AI_MODEL_ROOT="$MODEL_ROOT" \
    PYTHONPATH="$PROJECT_ROOT/backend" \
    "$PYTHON" -m app.migrate_cloned_voices

chown "$APP_USER:$APP_GROUP" \
    "$PROJECT_ROOT/backend/data/app.db"

log "Verifying Facebook and profile database schema"

run_as_user env \
    PYTHONPATH="$PROJECT_ROOT/backend" \
    YOUTUBE_AI_PROJECT_ROOT="$PROJECT_ROOT" \
    "$PYTHON" - <<'PYSCHEMA'
import sqlite3
from pathlib import Path

from app.config import settings

database_url = settings.database_url

prefix = "sqlite:///"

if not database_url.startswith(prefix):
    print("DATABASE_SCHEMA_CHECK_SKIPPED: non-SQLite database")
    raise SystemExit(0)

db_path = Path(database_url[len(prefix):])

connection = sqlite3.connect(db_path)

try:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    required_tables = {
        "users",
        "video_jobs",
        "facebook_connections",
        "facebook_publish_jobs",
        "cloned_voices",
        "tts_generations",
    }

    missing_tables = required_tables - tables

    if missing_tables:
        raise SystemExit(
            "Missing required database tables: "
            + ", ".join(sorted(missing_tables))
        )

    user_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(users)"
        )
    }

    required_user_columns = {
        "zernio_profile_id",
        "recovery_code_hash",
        "recovery_code_created_at",
    }

    missing_user_columns = (
        required_user_columns - user_columns
    )

    if missing_user_columns:
        raise SystemExit(
            "Missing users columns: "
            + ", ".join(sorted(missing_user_columns))
        )

    video_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(video_jobs)"
        )
    }

    required_video_columns = {
        "facebook_auto_publish_enabled",
        "facebook_auto_publish_connection_id",
        "facebook_auto_publish_scheduled_at",
        "cloned_voice_id",
    }

    missing_video_columns = (
        required_video_columns - video_columns
    )

    if missing_video_columns:
        raise SystemExit(
            "Missing video_jobs columns: "
            + ", ".join(sorted(missing_video_columns))
        )

finally:
    connection.close()

print("FACEBOOK_PROFILE_DATABASE_SCHEMA_OK")
PYSCHEMA

[[ -x "$PROJECT_ROOT/scripts/reset_user_password.py" ]] || \
    chmod +x "$PROJECT_ROOT/scripts/reset_user_password.py"

ENV_FILE="$ENV_FILE" "$PYTHON" - <<'PYCLEAR'
import os
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
lines = path.read_text().splitlines()

lines = [
    'INITIAL_ADMIN_PASSWORD=""'
    if line.startswith("INITIAL_ADMIN_PASSWORD=")
    else line
    for line in lines
]

path.write_text("\n".join(lines) + "\n")
PYCLEAR

chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
chmod 600 "$ENV_FILE"


log "Installing MuseTalk avatar engine"

run_as_user env \
    TORCH_HOME="$APP_HOME/.cache/torch" \
    bash \
    "$PROJECT_ROOT/scripts/setup_musetalk.sh" \
    "$APP_HOME/avatar-engine"

MUSETALK_ROOT="$APP_HOME/avatar-engine/MuseTalk"
MUSETALK_CONDA="$APP_HOME/avatar-engine/miniforge3/bin/conda"

[[ -d "$MUSETALK_ROOT" ]] || \
    fail "MuseTalk installation is missing."

[[ -x "$MUSETALK_CONDA" ]] || \
    fail "MuseTalk conda runtime is missing."

log "Installing permanent dashboard service"

cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=YouTube AI Video Generator
After=network-online.target redis-server.service ollama.service
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$PROJECT_ROOT
Environment=PYTHONPATH=$PROJECT_ROOT/backend
Environment=YOUTUBE_AI_PROJECT_ROOT=$PROJECT_ROOT
Environment=YOUTUBE_AI_MODEL_ROOT=$MODEL_ROOT
Environment=HF_TOKEN_PATH=$APP_HOME/.cache/huggingface/token
Environment=MUSETALK_ROOT=$APP_HOME/avatar-engine/MuseTalk
Environment=MUSETALK_CONDA=$APP_HOME/avatar-engine/miniforge3/bin/conda
Environment=MUSETALK_ENV=musetalk
Environment=LD_LIBRARY_PATH=$VENV/lib/python3.12/site-packages/nvidia/cublas/lib:$VENV/lib/python3.12/site-packages/nvidia/cudnn/lib
ExecStart=$VENV/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

log "Checking dashboard"

DASHBOARD_READY=0

for attempt in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        DASHBOARD_READY=1
        echo "Dashboard health check passed."
        break
    fi

    # Do not wait for the full timeout if the service has already crashed.
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        echo
        echo "Dashboard service exited during startup."
        journalctl -u "$SERVICE_NAME" -n 100 --no-pager || true
        fail "Dashboard service failed."
    fi

    if (( attempt % 5 == 0 )); then
        echo "Waiting for dashboard... (${attempt}/30)"
    fi

    sleep 2
done

if [[ "$DASHBOARD_READY" -ne 1 ]]; then
    journalctl -u "$SERVICE_NAME" -n 100 --no-pager || true
    fail "Dashboard did not become healthy within 60 seconds."
fi

log "Running final checks"

curl -fsS http://127.0.0.1:8000/health
echo

redis-cli ping

log "Checking current application modules"

run_as_user env \
    PYTHONPATH="$PROJECT_ROOT/backend" \
    YOUTUBE_AI_PROJECT_ROOT="$PROJECT_ROOT" \
    YOUTUBE_AI_MODEL_ROOT="$MODEL_ROOT" \
    "$PYTHON" - <<'PYAPP'
import app.main
import app.ai_pipeline
import app.script_workflow
import app.media_selector
import app.provider_quota
import app.youtube_metadata_generator
import app.youtube_thumbnail_generator
import app.title_research
import app.voice_registry
import app.avatar_service
import app.zernio_client
import app.facebook_connections
import app.facebook_auto_publish
import app.facebook_publish_queue
import app.facebook_publish_launcher
import app.facebook_publish_worker
import app.facebook_upload_runner

print("APPLICATION_IMPORTS_OK")
print("AVATAR_SERVICE_IMPORT_OK")
print("FACEBOOK_PUBLISHING_IMPORTS_OK")
PYAPP

ollama list | grep -F "$OLLAMA_MODEL" >/dev/null || \
    fail "Ollama model is missing."

log "Checking video encoder availability"

if ffmpeg \
    -hide_banner \
    -loglevel error \
    -nostdin \
    -f lavfi \
    -i "color=c=black:s=320x180:r=1" \
    -frames:v 1 \
    -an \
    -c:v h264_nvenc \
    -pix_fmt yuv420p \
    -f null - \
    >/dev/null 2>&1
then
    echo         "Video encoder: NVIDIA NVENC with automatic CPU fallback"
else
    echo         "Video encoder: CPU fallback (NVENC unavailable)"
fi

APP_URL_DISPLAY="$(
    ENV_FILE="$ENV_FILE" "$PYTHON" - <<'PYURL'
import json
import os
from pathlib import Path

path = Path(os.environ["ENV_FILE"])

for line in path.read_text().splitlines():
    if line.startswith("APP_URL="):
        raw = line.split("=", 1)[1]

        try:
            print(json.loads(raw))
        except Exception:
            print(raw.strip().strip('"'))

        break
else:
    print("http://127.0.0.1:8000")
PYURL
)"

echo
echo "==> Public dashboard setup"

DEFAULT_DOMAIN="attavideoai.site"
DEFAULT_CERTBOT_EMAIL="attarasool2000@gmail.com"

read -r -p "Domain [$DEFAULT_DOMAIN]: " INSTALL_DOMAIN
INSTALL_DOMAIN="${INSTALL_DOMAIN:-$DEFAULT_DOMAIN}"

read -r -p "Certbot email [$DEFAULT_CERTBOT_EMAIL]: " INSTALL_CERTBOT_EMAIL
INSTALL_CERTBOT_EMAIL="${INSTALL_CERTBOT_EMAIL:-$DEFAULT_CERTBOT_EMAIL}"

if [[ -n "$INSTALL_DOMAIN" && -n "$INSTALL_CERTBOT_EMAIL" ]]; then
    bash "$PROJECT_ROOT/scripts/setup_domain.sh" \
        "$INSTALL_DOMAIN" \
        "$INSTALL_CERTBOT_EMAIL"
fi

echo
echo "INSTALLATION_COMPLETE"
echo "Dashboard: $APP_URL_DISPLAY"
echo "Service: $SERVICE_NAME"
echo "Application user: $APP_USER"

if [[ "$RECOVERY_RESTORED" == "true" ]]; then
    echo "Environment and database restored from GitHub recovery backup."
fi

if [[ -n "$GENERATED_ADMIN_PASSWORD" ]]; then
    echo
    echo "Fresh administrator username: admin"
    echo "Fresh administrator password: $GENERATED_ADMIN_PASSWORD"
    echo "Save this password now. It will not be shown again."
fi
