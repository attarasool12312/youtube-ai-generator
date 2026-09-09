# ATTA VIDEO AI — Setup Guide

## 1. Recommended VM

- Ubuntu 24.04
- NVIDIA GPU
- 24 GB VRAM
- sudo access
- Internet connection

Check GPU:

```bash
nvidia-smi
```

## 2. Clone production branch

```bash
git clone -b openrelay-setup git@github.com:attarasool12312/youtube-ai-generator.git
cd youtube-ai-generator
```

## 3. Run one-command installer

```bash
sudo bash bootstrap.sh
```

Important ATTA-specific installer requirements:

- Z-Image stays in an **isolated virtual environment**
- current Z-Image target uses **PyTorch 2.11.0 + CUDA 12.8**
- do not replace the main application's pinned Torch stack with the Z-Image stack
- Remotion concurrency must follow the CPUs actually available to the process/VM
- systemd service must finish active

## 4. Verify

```bash
curl http://127.0.0.1:8000/health
systemctl is-active youtube-ai-generator
nvidia-smi
```

## 5. Open dashboard

```powershell
ssh -L 8000:127.0.0.1:8000 USER@SERVER
```

Open:

```text
http://127.0.0.1:8000
```

## 6. Configure APIs

Go to **API & Integrations**.

Recommended:

- Gemini
- Together AI
- Pexels
- Pixabay
- Coverr
- Openverse
- Google OAuth for YouTube
- Zernio for Facebook

See `API_SETUP.md`.

## 7. Configure YouTube

YouTube publishing requires Google Cloud + YouTube Data API v3 + Web OAuth client.

See `YOUTUBE_GOOGLE_CLOUD.md`.

## 8. Smoke tests

### Evergreen

```text
Why Interest Rates Control the Economy
```

Expected log:

```text
LIVE_RESEARCH|skip|evergreen
```

### Fresh/current

```text
Latest NASA Artemis II Update: What Happened and What Comes Next
```

Expected log:

```text
LIVE_RESEARCH|enabled
```

## 9. Unit tests

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests
```

## 10. Backup

```bash
bash scripts/backup_user_data.sh
```

Keep the archive private because it includes `.env` and the application database.
