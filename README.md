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
Local FLUX.1-schnell Image Generation  
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
- NVIDIA GPU with working drivers
- CUDA support
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
sudo bash install.sh
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


## Memory Configuration

The system uses FLUX.1-schnell for local image generation.

FLUX model loading requires additional memory.

Recommended VM configuration:

- RAM: 32GB+
- Swap: 24GB


Create swap:

```bash
sudo fallocate -l 24G /swapfile

sudo chmod 600 /swapfile

sudo mkswap /swapfile

sudo swapon /swapfile
```


Enable swap after reboot:

```bash
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```


Verify:

```bash
free -h
```


Without enough memory, Linux may terminate the image generation process during FLUX loading.


## AI Components

### Ollama

Local AI model runtime.

Check:

```bash
ollama list
```


Test:

```bash
ollama run qwen3:14b "Say hello"
```


### FLUX.1-schnell

Used for local image generation.

Model location:

```
~/models/flux1-schnell
```


Hugging Face cache:

```
~/models/huggingface
```


The system uses Diffusers with local model loading.


## Environment Configuration

Create `.env`:

```env
GEMINI_API_KEY=

OLLAMA_URL=http://127.0.0.1:11434

OLLAMA_MODEL=qwen3:14b

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
FLUX.1-schnell
```


Verified:

- Dashboard working
- AI generation working
- FLUX image generation working
- Video rendering working
- Video download working
- Recovery backup working


Keep this repository private.


## Developer

YouTube AI Video Generator by Atta Rasool

https://attarasool.com
