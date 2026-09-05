# YouTube AI Video Generator

Private GPU-powered AI video generation system for automated YouTube content creation.

## Overview

This system generates complete YouTube videos from a single title.

Pipeline:

Title Input
↓
AI Script Generation
↓
Scene Planning
↓
Stock Media Selection
↓
Kokoro TTS Voice Generation
↓
FFmpeg Video Rendering
↓
Final MP4 Output

## Private GitHub Repository Access

This repository is private.

Before installing on a new VM, GitHub access is required to clone the repository.

Use a GitHub Personal Access Token (PAT):

git clone -b openrelay-setup https://YOUR_GITHUB_USERNAME:YOUR_GITHUB_TOKEN@github.com/attarasool12312/youtube-ai-generator.git

cd youtube-ai-generator

GitHub credentials are only required for the initial repository download.

They are not stored in:

- Recovery backups
- Database
- Environment files

## Fresh VM Setup
### Requirements

- Ubuntu 24.04
- NVIDIA GPU-enabled VM
- Python 3.12
- sudo access
- Private GitHub repository access


Check GPU:

```bash
nvidia-smi
```


Clone repository:

```bash
git clone -b openrelay-setup git@github.com:attarasool12312/youtube-ai-generator.git

cd youtube-ai-generator
```


Run installer:

```bash
sudo bash bootstrap.sh
```


The installer configures:

- Python environment
- Required packages
- Redis
- Ollama
- AI models
- Systemd service
- Application environment


Successful installation:

```
INSTALLATION_COMPLETE
```


## AI Components



### Ollama

Local AI model runtime.

Check:

```bash
ollama list
```


Test:

```bash
ollama run qwen3:30b "Say hello"
```


## Environment Configuration

Create `.env`:

```env
GEMINI_API_KEY=

OLLAMA_URL=http://127.0.0.1:11434

OLLAMA_MODEL=qwen3:30b

SETTINGS_ENCRYPTION_KEY=
```


## Database

The application uses SQLite.

Database location:

```
backend/data/app.db
```


Stores:

- Users
- Video jobs
- Application settings
- API configuration


## Redis Queue

Redis manages background processing.

Check:

```bash
redis-cli ping
```

Expected:

```
PONG
```


## Running Application

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```


## Dashboard Access

Create SSH tunnel:

```powershell
ssh -L 8000:127.0.0.1:8000 YOUR_VM_USERNAME@YOUR_VM_HOST
```


Open:

```
http://127.0.0.1:8000
```


## System Service Commands

Check status:

```bash
sudo systemctl status youtube-ai-generator --no-pager
```


Restart:

```bash
sudo systemctl restart youtube-ai-generator
```


View logs:

```bash
sudo journalctl -u youtube-ai-generator -n 100 --no-pager
```


## Video Output

Generated videos are stored:

```
outputs/jobs/{job_id}/
```


Example:

```
final-video.mp4
media/
render-clips/
```


## Video Download

Large video downloads use streaming delivery.

Benefits:

- Lower memory usage
- Stable downloads
- Dashboard remains responsive
- Better handling of large MP4 files


## Backup and Recovery

Create backup:

```bash
./backup.sh
```


Successful output:

```
BACKUP_COMPLETE
```


Backup includes:

- Application configuration
- Database
- Runtime settings
- Administrator data


Excluded:

- Generated videos
- Model cache files


## Tested Environment

GPU:

```
NVIDIA RTX 5090
```


CUDA:

```
13.0
```


Python:

```
3.12
```


AI Models:

```
Qwen3:14B
Stock Media
```


Verified:

- Dashboard working
- AI generation working
- Stock media generation pipeline working
- Video rendering working
- Video download working
- Recovery backup working


Keep this repository private.


## Developer

YouTube AI Video Generator by Atta Rasool

https://attarasool.com

## Tested Production Checkpoint — 2026-08-01

The following features were completed on the OpenRelay RTX 4090 deployment. The final full workflow test used Stock media:

- Stock Only media-source mode
- Improved Pixabay and stock-media selection
- Duplicate-media prevention
- Static image fades without unwanted zoom
- Gemini free-model rotation with Ollama and Together fallbacks
- Encrypted API-key add, replace, and remove controls
- Queue progress stages, readable statuses, ETA, retry, and cancel
- Single sequential GPU-heavy job processing
- Detailed failure messages
- Automatic render temporary-file cleanup
- Captions enabled by default with small font size
- Quiet dashboard polling without full-page reloads
- Browser desktop notifications for job started, completed, and failed
- Bottom-left dashboard popup notifications with optional sound

A complete 3-minute landscape video using Stock media and captions was generated and verified successfully.

The active deployment and recovery branch is:

```text
openrelay-setup
```

The recovery archive is stored in:

`recovery/runtime-state.tar.gz`

## One-Command Fresh GPU VM Setup

After cloning the `openrelay-setup` branch and entering the project directory, run:

    sudo bash bootstrap.sh

The bootstrap checks the NVIDIA GPU and driver. If the driver is working, it continues with the existing `install.sh`. If it installs a driver and requests a reboot, reboot and run the same command again.

Successful setup ends with:

    INSTALLATION_COMPLETE
