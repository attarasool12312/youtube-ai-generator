#!/usr/bin/env bash
# Restore lightweight ATTA user/runtime backup.
# Existing important files are preserved under pre-restore-* first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="${1:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"
KEEP="$ROOT/backend/data/pre-restore-$STAMP"

if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "Usage: bash scripts/restore_user_data.sh /path/to/atta-user-data-*.tar.gz"
    exit 1
fi

if [ -f "$ARCHIVE.sha256" ]; then
    (
        cd "$(dirname "$ARCHIVE")"
        sha256sum -c "$(basename "$ARCHIVE").sha256"
    ) || {
        echo "CHECKSUM_FAILED"
        exit 1
    }
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Validate archive before touching live data.
tar -tzf "$ARCHIVE" >/dev/null
tar -xzf "$ARCHIVE" -C "$WORK"

SRC="$WORK/user-data"
[ -d "$SRC" ] || {
    echo "BAD_ARCHIVE: user-data folder missing"
    exit 1
}

# Backward compatibility with older archive layout.
if [ -d "$SRC/backend-data" ]; then
    SRC_DATA="$SRC/backend-data"
else
    SRC_DATA="$SRC"
fi

RESTART=0
if systemctl is-active --quiet youtube-ai-generator 2>/dev/null; then
    echo "Stopping youtube-ai-generator service"
    sudo systemctl stop youtube-ai-generator
    RESTART=1
fi

mkdir -p "$KEEP" "$ROOT/backend/data"

# Preserve current live state first.
for name in \
    app.db \
    gemini_router_state.json \
    media_history.json \
    video_presets.json
do
    if [ -f "$ROOT/backend/data/$name" ]; then
        cp -a "$ROOT/backend/data/$name" "$KEEP/$name"
    fi
done

[ -f "$ROOT/.env" ] && cp -a "$ROOT/.env" "$KEEP/.env"

# Preserve cloned voices before replacing them.
if [ -d "$ROOT/backend/app/static/cloned-voices" ]; then
    mkdir -p "$KEEP/cloned-voices"
    cp -a "$ROOT/backend/app/static/cloned-voices/." "$KEEP/cloned-voices/"
fi

# A restored SQLite database must not inherit WAL/SHM files from the
# destination VM. Stale WAL data can otherwise override restored rows.
if [ -f "$SRC_DATA/app.db" ]; then
    rm -f "$ROOT/backend/data/app.db-wal" "$ROOT/backend/data/app.db-shm"
fi

# Restore available files only.
for name in \
    app.db \
    gemini_router_state.json \
    media_history.json \
    video_presets.json
do
    if [ -f "$SRC_DATA/$name" ]; then
        cp "$SRC_DATA/$name" "$ROOT/backend/data/$name"
    fi
done

[ -f "$SRC/.env" ] && cp "$SRC/.env" "$ROOT/.env"

# Restore cloned voice audio. Avatars are intentionally not restored.
if [ -d "$SRC/cloned-voices" ]; then
    TARGET="$ROOT/backend/app/static/cloned-voices"
    rm -rf "$TARGET"
    mkdir -p "$TARGET"
    cp -a "$SRC/cloned-voices/." "$TARGET/"
fi

# Make cloned-voice DB paths portable between VMs/project locations.
if command -v sqlite3 >/dev/null 2>&1 && [ -f "$ROOT/backend/data/app.db" ]; then
    sqlite3 "$ROOT/backend/data/app.db" "
        UPDATE cloned_voices
        SET reference_audio_path =
            '$ROOT/backend/app/static/cloned-voices/' || id || '/reference.wav'
        WHERE reference_audio_path IS NOT NULL;

        UPDATE cloned_voices
        SET preview_audio_path =
            '$ROOT/backend/app/static/cloned-voices/' || id || '/preview.mp3'
        WHERE preview_audio_path IS NOT NULL;
    " 2>/dev/null || true
fi

OWNER="$(stat -c '%U:%G' "$ROOT")"

if [ "$(id -u)" = "0" ] && [ "$OWNER" != "root:root" ]; then
    chown -R "$OWNER" "$ROOT/backend/data" 2>/dev/null || true
    [ -d "$ROOT/backend/app/static/cloned-voices" ] && \
        chown -R "$OWNER" "$ROOT/backend/app/static/cloned-voices" 2>/dev/null || true
    [ -f "$ROOT/.env" ] && chown "$OWNER" "$ROOT/.env"
fi

if [ "$RESTART" = "1" ]; then
    sudo systemctl start youtube-ai-generator
    sleep 3
    curl -fsS http://127.0.0.1:8000/health || true
    echo
fi

echo "RESTORE_OK previous data kept in $KEEP"
