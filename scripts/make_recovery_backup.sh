#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$ROOT/backups/recovery-$STAMP"
ARCHIVE="$ROOT/backups/atta-video-ai-recovery-$STAMP.tar.gz"

mkdir -p \
    "$WORK/runtime" \
    "$WORK/system"

echo "=== CREATING RECOVERY CHECKPOINT ==="

tar \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='backups' \
    --exclude='outputs' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -czf "$WORK/repo-source.tar.gz" \
    -C "$ROOT" .

[ -f "$ROOT/.env" ] && \
    cp "$ROOT/.env" "$WORK/runtime/.env"

[ -f "$ROOT/backend/data/app.db" ] && \
    cp "$ROOT/backend/data/app.db" \
       "$WORK/runtime/app.db"

[ -f "$ROOT/requirements.txt" ] && \
    cp "$ROOT/requirements.txt" \
       "$WORK/runtime/requirements.txt"

"$ROOT/.venv/bin/pip" freeze \
    > "$WORK/runtime/pip-freeze.txt" 2>/dev/null || true

git -C "$ROOT" branch --show-current \
    > "$WORK/runtime/git-branch.txt"

git -C "$ROOT" rev-parse HEAD \
    > "$WORK/runtime/git-head.txt"

git -C "$ROOT" status --short \
    > "$WORK/runtime/git-status.txt"

git -C "$ROOT" diff \
    > "$WORK/runtime/uncommitted.patch"

curl -sS https://attavideoai.site/health \
    > "$WORK/runtime/health.txt" 2>/dev/null || true

sudo cp \
    /etc/systemd/system/youtube-ai-generator.service \
    "$WORK/system/youtube-ai-generator.service" \
    2>/dev/null || true

sudo cp \
    /etc/nginx/sites-available/attavideoai.site \
    "$WORK/system/nginx-attavideoai.site" \
    2>/dev/null || true

sudo tar -czf \
    "$WORK/system/letsencrypt-private.tar.gz" \
    /etc/letsencrypt 2>/dev/null || true

sudo chown -R "$USER":"$USER" "$WORK"

tar -czf "$ARCHIVE" \
    -C "$WORK" .

sha256sum "$ARCHIVE" \
    > "${ARCHIVE}.sha256"

echo
echo "RECOVERY_ARCHIVE=$ARCHIVE"
echo "CHECKSUM_FILE=${ARCHIVE}.sha256"
echo "RECOVERY_BACKUP_COMPLETE"
