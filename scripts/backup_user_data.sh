#!/usr/bin/env bash
# Portable user-data backup: database (incl. encrypted OAuth tokens), .env
# (holds the decryption key - keep the archive private), avatars, cloned-voice
# reference audio. Excludes generated videos, narration, caches.
# Usage: bash scripts/backup_user_data.sh [output-directory]
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$DEST/atta-user-data-$STAMP.tar.gz"

mkdir -p "$DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/user-data"

if [ -f "$ROOT/backend/data/app.db" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$ROOT/backend/data/app.db" ".backup '$WORK/user-data/app.db'"
    else
        cp "$ROOT/backend/data/app.db" "$WORK/user-data/app.db"
    fi
else
    echo "WARNING: backend/data/app.db not found"
fi

[ -f "$ROOT/.env" ] && cp "$ROOT/.env" "$WORK/user-data/.env"

[ -d "$ROOT/backend/data/avatars" ] && \
    cp -r "$ROOT/backend/data/avatars" "$WORK/user-data/avatars"

[ -d "$ROOT/backend/app/static/cloned-voices" ] && \
    cp -r "$ROOT/backend/app/static/cloned-voices" "$WORK/user-data/cloned-voices"

cat > "$WORK/user-data/MANIFEST.txt" <<MANIFEST
atta-video-ai user data backup
created: $STAMP
host: $(hostname)
git: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)
contents: app.db .env avatars/ cloned-voices/
restore: bash scripts/restore_user_data.sh <archive>
MANIFEST

tar -czf "$ARCHIVE" -C "$WORK" user-data
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

echo "BACKUP_OK $ARCHIVE"
du -h "$ARCHIVE" | cut -f1
