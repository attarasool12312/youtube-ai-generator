# ATTA VIDEO AI

Private GPU-powered AI video production system for creating long-form YouTube and Facebook videos from a title or a finished script.

> Updated for the current ATTA production architecture and provider setup as of September 2026.

## Production pipeline

**Title / Script → Live Research when needed → Script → Scene Planning → Stock Media → Z-Image fallback → TTS → Captions → Remotion/FFmpeg Render → Thumbnail Prompt → YouTube/Facebook Publishing**

Current routing:

- **Long-form script:** DeepSeek V4 Flash
- **Script fallback / repair:** DeepSeek V4 Pro
- **Last script fallback:** Gemini
- **Metadata:** Gemini
- **Scene search queries / Z-Image prompts:** Gemini
- **Fresh-data research:** Gemini + Google Search grounding for time-sensitive topics
- **Media order:** stock video → stock image → Z-Image Turbo
- **Captions:** Whisper timings + Remotion styling
- **Publishing:** YouTube OAuth + Facebook through Zernio

## Recommended server

- Ubuntu 24.04
- NVIDIA GPU
- **24 GB VRAM recommended**
- sudo/root access
- Internet access
- Private GitHub repository access

## Fresh VM installation

```bash
git clone -b openrelay-setup git@github.com:attarasool12312/youtube-ai-generator.git
cd youtube-ai-generator
sudo bash bootstrap.sh
```

The target is a **single-command setup** for the complete production stack.

After installation:

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

## Dashboard access

For local tunnel access:

```powershell
ssh -L 8000:127.0.0.1:8000 USER@SERVER
```

Open:

```text
http://127.0.0.1:8000
```

## First setup

Open **Dashboard → API & Integrations** and configure the providers you need:

1. Google Gemini
2. Together AI
3. Pexels
4. Pixabay
5. Coverr
6. Openverse Client ID + Client Secret
7. Google OAuth credentials for YouTube publishing
8. Zernio API key for Facebook publishing

Detailed guides:

- [Setup Guide](docs/SETUP_GUIDE.md)
- [API Setup](docs/API_SETUP.md)
- [Google Cloud & YouTube OAuth](docs/YOUTUBE_GOOGLE_CLOUD.md)
- [Backup & Restore](docs/BACKUP_RESTORE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Production Checklist](docs/PRODUCTION_CHECKLIST.md)

## Saved presets

ATTA supports reusable video-generation presets. Presets intentionally do **not** save:

- title
- titles list
- source script

Server-side preset state:

```text
backend/data/video_presets.json
```

## Backup

From the dashboard use **Backup & Restore**, or run:

```bash
bash scripts/backup_user_data.sh
```

The lightweight backup includes:

- `backend/data/app.db`
- `.env`
- `backend/data/gemini_router_state.json`
- `backend/data/media_history.json`
- `backend/data/video_presets.json` when present

Generated videos, model caches, avatars and cloned-voice media are intentionally excluded.

## Tests

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests
```

## Service commands

```bash
sudo systemctl status youtube-ai-generator --no-pager
sudo systemctl restart youtube-ai-generator
sudo journalctl -u youtube-ai-generator -n 200 --no-pager
```

## Security

Never commit or publish `.env`, API keys, OAuth client secrets, refresh/access tokens, encryption keys, production databases or private backup archives.

## Production branch

```text
openrelay-setup
```
