#!/usr/bin/env bash
# Restore a user-data archive made by backup_user_data.sh.
# Never deletes existing data: current db/.env/avatars/cloned-voices are moved
# to backend/data/pre-restore-<stamp>/ first.
# Usage: bash scripts/restore_user_data.sh /path/to/atta-user-data-*.tar.gz
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="${1:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"
KEEP="$ROOT/backend/data/pre-restore-$STAMP"

if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "Usage: bash scripts/restore_user_data.sh /path/to/atta-user-data-*.tar.gz"
    exit 1
fi

if [ -f "$ARCHIVE.sha256" ]; then
    ( cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256" ) || {
        echo "CHECKSUM_FAILED"; exit 1; }
fi

if systemctl is-active --quiet youtube-ai-generator 2>/dev/null; then
    echo "Stopping youtube-ai-generator service (sudo)"
    sudo systemctl stop youtube-ai-generator
    RESTART=1
else
    RESTART=0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -xzf "$ARCHIVE" -C "$WORK"

SRC="$WORK/user-data"
[ -d "$SRC" ] || { echo "BAD_ARCHIVE: user-data folder missing"; exit 1; }

mkdir -p "$KEEP" "$ROOT/backend/data" "$ROOT/backend/app/static"

[ -f "$ROOT/backend/data/app.db" ] && mv "$ROOT/backend/data/app.db" "$KEEP/app.db"
[ -f "$ROOT/.env" ] && cp "$ROOT/.env" "$KEEP/.env"
[ -d "$ROOT/backend/data/avatars" ] && mv "$ROOT/backend/data/avatars" "$KEEP/avatars"
[ -d "$ROOT/backend/app/static/cloned-voices" ] && \
    mv "$ROOT/backend/app/static/cloned-voices" "$KEEP/cloned-voices"

[ -f "$SRC/app.db" ] && cp "$SRC/app.db" "$ROOT/backend/data/app.db"
[ -f "$SRC/.env" ] && cp "$SRC/.env" "$ROOT/.env"
[ -d "$SRC/avatars" ] && cp -r "$SRC/avatars" "$ROOT/backend/data/avatars"
[ -d "$SRC/cloned-voices" ] && cp -r "$SRC/cloned-voices" "$ROOT/backend/app/static/cloned-voices"

# When invoked via sudo/systemd-run the copies land root-owned and the app
# (running as the project owner) cannot write its own database. Hand the
# files back to whoever owns the project directory.
OWNER="$(stat -c '%U:%G' "$ROOT")"
if [ "$(id -u)" = "0" ] && [ "$OWNER" != "root:root" ]; then
    chown -R "$OWNER"         "$ROOT/backend/data"         "$ROOT/backend/app/static/cloned-voices" 2>/dev/null || true
    [ -f "$ROOT/.env" ] && chown "$OWNER" "$ROOT/.env"
fi

if [ "$RESTART" = "1" ]; then
    sudo systemctl start youtube-ai-generator
    sleep 3
    curl -s http://127.0.0.1:8000/health || true
    echo
fi

echo "RESTORE_OK previous data kept in $KEEP"
