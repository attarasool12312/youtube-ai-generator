# YouTube AI Video Generator

Private GPU-powered YouTube video generation system.

## Fresh VM Setup

Requirements:
- Ubuntu 24.04
- NVIDIA GPU and working driver
- sudo access
- private GitHub repository access

Verify the GPU:

```bash
nvidia-smi
```

Clone and install:

```bash
git clone -b openrelay-setup git@github.com:attarasool12312/youtube-ai-generator.git
cd youtube-ai-generator
sudo bash install.sh
```

The installer automatically restores:
- `.env`
- SQLite database
- administrator account
- saved API settings
- settings encryption key
- Hugging Face token

It also installs:
- Ubuntu dependencies
- Python virtual environment and packages
- Redis
- Ollama
- Qwen model
- FLUX.1-schnell
- systemd service

Successful installation ends with:

```text
INSTALLATION_COMPLETE
```

Check health:

```bash
curl -fsS http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

Access from Windows PowerShell:

```powershell
ssh -L 8000:127.0.0.1:8000 YOUR_VM_USERNAME@YOUR_VM_HOST
```

Then open:

```text
http://127.0.0.1:8000
```

## Backup

Create and push a new recovery backup:

```bash
cd ~/youtube-ai-generator
./backup.sh
```

Successful output:

```text
BACKUP_COMPLETE
```

## Service Commands

```bash
sudo systemctl status youtube-ai-generator --no-pager
sudo systemctl restart youtube-ai-generator
sudo journalctl -u youtube-ai-generator -n 100 --no-pager
redis-cli ping
ollama list
nvidia-smi
```

## Recovery Verification

```bash
sha256sum -c recovery/runtime-state.sha256
tar -tzf recovery/runtime-state.tar.gz
```

The recovery archive contains `.env`, the SQLite database, saved settings, administrator data, and the Hugging Face token.

Generated videos and downloadable models are excluded.

Keep this repository private.

## Developer

YouTube AI Video Generator by Atta Rasool  
https://attarasool.com
