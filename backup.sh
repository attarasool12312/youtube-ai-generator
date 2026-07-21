#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HF_TOKEN_FILE="${HF_TOKEN_FILE:-$HOME/.cache/huggingface/token}"
[[ -s "$HF_TOKEN_FILE" ]] || {
    echo "Hugging Face token is missing: $HF_TOKEN_FILE" >&2
    exit 1
}

BRANCH="$(git branch --show-current)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/backend/data" "$TMP/systemd" "$TMP/huggingface"

install -m 600 .env "$TMP/.env"
install -m 600 "$HF_TOKEN_FILE" "$TMP/huggingface/token"

python3 - backend/data/app.db "$TMP/backend/data/app.db" <<'PY'
import sqlite3
import sys

source, destination = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
dst = sqlite3.connect(destination)

with dst:
    src.backup(dst)

src.close()
dst.close()
PY

cp install.sh backend/requirements.txt "$TMP/"

if sudo -n test -f /etc/systemd/system/youtube-ai-generator.service; then
    sudo -n cat /etc/systemd/system/youtube-ai-generator.service \
        > "$TMP/systemd/youtube-ai-generator.service"
fi

.venv/bin/pip freeze > "$TMP/pip-freeze.txt"
ollama list > "$TMP/ollama-models.txt" 2>/dev/null || true

{
    echo "Created: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "Commit: $(git rev-parse HEAD)"
    echo "Branch: $BRANCH"
    echo "Ubuntu: $(lsb_release -ds 2>/dev/null || true)"
    nvidia-smi --query-gpu=name,memory.total,driver_version \
        --format=csv,noheader 2>/dev/null || true
} > "$TMP/manifest.txt"

mkdir -p recovery
tar -C "$TMP" -czf recovery/runtime-state.tar.gz .
sha256sum recovery/runtime-state.tar.gz \
    > recovery/runtime-state.sha256

git add -f recovery/runtime-state.tar.gz recovery/runtime-state.sha256

if ! git diff --cached --quiet; then
    git commit -m "Update complete disaster recovery state"
fi

git push origin "$BRANCH"

echo "BACKUP_COMPLETE"
ls -lh recovery/runtime-state.tar.gz
cat recovery/runtime-state.sha256
