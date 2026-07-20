#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "Installation failed near line $LINENO."' ERR

log() {
    echo
    echo "==> $*"
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"
CURRENT_HOME="$(getent passwd "$CURRENT_USER" | cut -d: -f6)"
MODEL_ROOT="${YOUTUBE_AI_MODEL_ROOT:-$(dirname "$PROJECT_ROOT")/models}"
VENV="$PROJECT_ROOT/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"
OLLAMA_MODEL="qwen3:14b"

log "Checking sudo -n access"
sudo -n true || fail "Passwordless sudo -n is required."

log "Installing Ubuntu system dependencies"
sudo -n apt-get update
sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl git ffmpeg espeak-ng redis-server \
    python3 python3-venv python3-pip python3-dev build-essential \
    libsndfile1 libffi-dev libssl-dev pkg-config sqlite3 unzip

command -v nvidia-smi >/dev/null || \
    fail "NVIDIA driver/GPU was not detected."

log "GPU detected"
nvidia-smi --query-gpu=name,memory.total \
    --format=csv,noheader

log "Creating project directories"
mkdir -p \
    "$MODEL_ROOT/huggingface" \
    "$PROJECT_ROOT/backend/data" \
    "$PROJECT_ROOT/outputs/jobs" \
    "$PROJECT_ROOT/logs"

log "Creating Python virtual environment"
/usr/bin/python3 -m venv "$VENV"
"$PIP" install --upgrade pip setuptools wheel
"$PIP" install -r "$PROJECT_ROOT/backend/requirements.txt"
"$PIP" check

log "Starting Redis"
sudo -n systemctl enable --now redis-server
redis-cli ping | grep -q PONG || fail "Redis did not start."

if ! command -v ollama >/dev/null; then
    log "Installing Ollama"
    curl -fsSL https://ollama.com/install.sh | sh
fi

log "Starting Ollama"
sudo -n systemctl enable --now ollama

for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:11434/api/tags >/dev/null && break
    sleep 2
done

curl -fsS http://127.0.0.1:11434/api/tags >/dev/null || \
    fail "Ollama did not start."

log "Downloading Ollama model: $OLLAMA_MODEL"
ollama pull "$OLLAMA_MODEL"

log "Preparing application environment"
ENV_FILE="$PROJECT_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    read -rp "Admin username [admin]: " ADMIN_USERNAME
    ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"

    read -rp "Admin email: " ADMIN_EMAIL
    [[ -n "$ADMIN_EMAIL" ]] || fail "Admin email is required."

    read -rsp "Admin password (minimum 8 characters): " ADMIN_PASSWORD
    echo
    [[ ${#ADMIN_PASSWORD} -ge 8 ]] || fail "Password is too short."

    read -rp "App URL [http://127.0.0.1:8000]: " APP_URL
    APP_URL="${APP_URL:-http://127.0.0.1:8000}"

    SECRET_KEY="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    FERNET_KEY="$("$PYTHON" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

    export PROJECT_ROOT ADMIN_USERNAME ADMIN_EMAIL ADMIN_PASSWORD
    export APP_URL SECRET_KEY FERNET_KEY OLLAMA_MODEL

    "$PYTHON" - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["PROJECT_ROOT"])
values = {
    "APP_NAME": "YouTube AI Video Generator",
    "APP_URL": os.environ["APP_URL"],
    "SECRET_KEY": os.environ["SECRET_KEY"],
    "SETTINGS_ENCRYPTION_KEY": os.environ["FERNET_KEY"],
    "ADMIN_USERNAME": os.environ["ADMIN_USERNAME"],
    "ADMIN_EMAIL": os.environ["ADMIN_EMAIL"],
    "INITIAL_ADMIN_PASSWORD": os.environ["ADMIN_PASSWORD"],
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
PY

    chmod 600 "$ENV_FILE"
    unset ADMIN_PASSWORD
else
    log "Existing .env preserved"
fi

log "Initializing database and administrator"
YOUTUBE_AI_PROJECT_ROOT="$PROJECT_ROOT" \
YOUTUBE_AI_MODEL_ROOT="$MODEL_ROOT" \
PYTHONPATH="$PROJECT_ROOT/backend" \
"$PYTHON" -m app.init_db

ENV_FILE="$ENV_FILE" "$PYTHON" - <<'PY'
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
PY

log "Preparing FLUX.1-schnell"
FLUX_DIR="$MODEL_ROOT/flux1-schnell"

if [[ ! -f "$FLUX_DIR/model_index.json" ]]; then
    echo "Accept model access in your browser when requested."
    echo "Model page: https://huggingface.co/black-forest-labs/FLUX.1-schnell"

    "$VENV/bin/hf" auth login --force

    MODEL_ROOT="$MODEL_ROOT" "$PYTHON" - <<'PY'
import os
from pathlib import Path
from huggingface_hub import snapshot_download

target = Path(os.environ["MODEL_ROOT"]) / "flux1-schnell"
target.mkdir(parents=True, exist_ok=True)

snapshot_download(
    repo_id="black-forest-labs/FLUX.1-schnell",
    local_dir=str(target),
    token=True,
    max_workers=4,
)
PY
else
    log "Existing FLUX model preserved"
fi

[[ -f "$FLUX_DIR/model_index.json" ]] || \
    fail "FLUX model download is incomplete."

log "Installing permanent dashboard service"
SERVICE_NAME="youtube-ai-generator"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_GROUP="$(id -gn "$CURRENT_USER")"

sudo -n tee "$SERVICE_FILE" >/dev/null <<SERVICE
[Unit]
Description=YouTube AI Video Generator
After=network-online.target redis-server.service ollama.service
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_GROUP
WorkingDirectory=$PROJECT_ROOT
Environment=PYTHONPATH=$PROJECT_ROOT/backend
Environment=YOUTUBE_AI_PROJECT_ROOT=$PROJECT_ROOT
Environment=YOUTUBE_AI_MODEL_ROOT=$MODEL_ROOT
ExecStart=$VENV/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo -n systemctl daemon-reload
sudo -n systemctl enable --now "$SERVICE_NAME"

log "Checking dashboard"
for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:8000/health >/dev/null && break
    sleep 2
done

curl -fsS http://127.0.0.1:8000/health >/dev/null || {
    sudo -n journalctl -u "$SERVICE_NAME" -n 60 --no-pager
    fail "Dashboard service failed."
}

echo "Dashboard service is ready."

read -rp "Configure a permanent Cloudflare Tunnel? [y/N]: " SETUP_TUNNEL
if [[ "${SETUP_TUNNEL,,}" == "y" ]]; then
    if ! command -v cloudflared >/dev/null; then
        log "Installing cloudflared"
        ARCH="$(dpkg --print-architecture)"
        [[ "$ARCH" == "i386" ]] && ARCH="386"
        curl -fL \
          "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb" \
          -o /tmp/cloudflared.deb
        sudo -n dpkg -i /tmp/cloudflared.deb || sudo -n apt-get -f install -y
    fi

    echo
    echo "In Cloudflare: Networking > Tunnels > Create tunnel."
    echo "Add a Published Application using: http://localhost:8000"
    echo "Then copy only the generated tunnel token."

    if systemctl list-unit-files cloudflared.service \
        --no-legend 2>/dev/null | grep -q cloudflared; then
        log "Existing cloudflared service preserved"
    else
        read -rsp "Cloudflare tunnel token: " TUNNEL_TOKEN
        echo
        [[ -n "$TUNNEL_TOKEN" ]] || fail "Tunnel token is required."
        sudo -n cloudflared service install "$TUNNEL_TOKEN"
        unset TUNNEL_TOKEN
    fi

    read -rp "Public URL, for example https://video.example.com: " PUBLIC_URL
    [[ "$PUBLIC_URL" == https://* ]] || \
        fail "Public URL must start with https://"

    PUBLIC_URL="$PUBLIC_URL" ENV_FILE="$ENV_FILE" "$PYTHON" - <<'PYURL'
import json
import os
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
url = os.environ["PUBLIC_URL"]
lines = path.read_text().splitlines()
updated = []
found = False

for line in lines:
    if line.startswith("APP_URL="):
        updated.append(f"APP_URL={json.dumps(url)}")
        found = True
    else:
        updated.append(line)

if not found:
    updated.append(f"APP_URL={json.dumps(url)}")

path.write_text("\n".join(updated) + "\n")
PYURL

    sudo -n systemctl enable --now cloudflared
    sudo -n systemctl restart "$SERVICE_NAME"
    echo "Live dashboard: $PUBLIC_URL"
fi

log "Running final checks"
curl -fsS http://127.0.0.1:8000/health
redis-cli ping
ollama list | grep -F "$OLLAMA_MODEL" >/dev/null || \
    fail "Ollama model is missing."

echo
echo "Installation completed successfully."
echo "Local dashboard: http://127.0.0.1:8000"
echo "API keys can now be entered from the dashboard Settings page."
