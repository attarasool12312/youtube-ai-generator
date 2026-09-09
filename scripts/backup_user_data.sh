#!/usr/bin/env bash
# Portable lightweight ATTA user/runtime backup.
# Includes database, secrets/config and small runtime state only.
# Excludes generated videos, caches, avatars and cloned-voice media.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$DEST/atta-user-data-$STAMP.tar.gz"

mkdir -p "$DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

DATA="$WORK/user-data"
mkdir -p "$DATA/backend-data"

if [ -f "$ROOT/backend/data/app.db" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$ROOT/backend/data/app.db" \
            ".backup '$DATA/backend-data/app.db'"
    else
        cp "$ROOT/backend/data/app.db" \
           "$DATA/backend-data/app.db"
    fi
else
    echo "WARNING: backend/data/app.db not found"
fi

[ -f "$ROOT/.env" ] && \
    cp "$ROOT/.env" "$DATA/.env"

for name in \
    gemini_router_state.json \
    media_history.json \
    video_presets.json
do
    [ -f "$ROOT/backend/data/$name" ] && \
        cp "$ROOT/backend/data/$name" \
           "$DATA/backend-data/$name"
done

cat > "$DATA/MANIFEST.txt" <<MANIFEST
ATTA VIDEO AI lightweight user/runtime backup
created: $STAMP
host: $(hostname)
git: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)

Included:
- backend/data/app.db
- .env
- gemini_router_state.json (when present)
- media_history.json (when present)
- video_presets.json (when present)

Excluded:
- generated videos/output files
- avatars
- cloned voice media
- model caches
- virtual environments
MANIFEST

tar -czf "$ARCHIVE" -C "$WORK" user-data
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

echo "BACKUP_OK $ARCHIVE"
du -h "$ARCHIVE" | cut -f1
