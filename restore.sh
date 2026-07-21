#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "Run with: sudo bash restore.sh"
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ARCHIVE="$ROOT/recovery/runtime-state.tar.gz"
CHECKSUM="$ROOT/recovery/runtime-state.sha256"

[[ -f "$ARCHIVE" ]] || {
    echo "Recovery archive missing."
    exit 1
}

[[ -f "$CHECKSUM" ]] || {
    echo "Recovery checksum missing."
    exit 1
}

sha256sum -c "$CHECKSUM"

APP_USER="${SUDO_USER:-ubuntu}"

if ! id "$APP_USER" >/dev/null 2>&1; then
    echo "Application user not found: $APP_USER"
    exit 1
fi

APP_GROUP="$(id -gn "$APP_USER")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

tar -xzf "$ARCHIVE" -C "$TMP"

chown -R "$APP_USER:$APP_GROUP" "$ROOT"

install -o "$APP_USER" -g "$APP_GROUP" -m 600 \
    "$TMP/.env" "$ROOT/.env"

mkdir -p "$ROOT/backend/data"

install -o "$APP_USER" -g "$APP_GROUP" -m 600 \
    "$TMP/backend/data/app.db" \
    "$ROOT/backend/data/app.db"

sudo -u "$APP_USER" -H bash "$ROOT/install.sh"

systemctl daemon-reload
systemctl enable --now redis-server ollama youtube-ai-generator

for attempt in {1..30}; do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
        echo
        echo "RESTORE_COMPLETE"
        curl -fsS http://127.0.0.1:8000/health
        echo
        exit 0
    fi

    sleep 2
done

echo "Restore finished, but dashboard health check failed."
systemctl status youtube-ai-generator --no-pager -l || true
exit 1
