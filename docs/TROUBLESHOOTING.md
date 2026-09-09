# Troubleshooting

## Service not running

```bash
systemctl is-active youtube-ai-generator
sudo journalctl -u youtube-ai-generator -n 200 --no-pager
sudo systemctl restart youtube-ai-generator
```

## Internal Server Error

```bash
curl http://127.0.0.1:8000/health
sudo journalctl -u youtube-ai-generator -n 200 --no-pager
```

## Z-Image CUDA unavailable

ATTA keeps Z-Image in an isolated environment.

Current production target:

```text
PyTorch 2.11.0 + cu128
```

Do not fix Z-Image by changing the main application's Torch stack.

Check the NVIDIA driver:

```bash
nvidia-smi
```

## NVIDIA driver / CUDA mismatch

A Torch wheel built for a newer CUDA runtime than the host driver supports can cause `torch.cuda.is_available()` to be false.

The current installer fix pins the isolated Z-Image environment to CUDA 12.8-compatible PyTorch.

## Remotion concurrency error

If Remotion says concurrency is higher than available CPU cores, do not hardcode 8x. ATTA should derive concurrency from CPUs actually available to the process/VM.

## Gemini key issue

For a new setup, create a current key in Google AI Studio:

https://ai.google.dev/gemini-api/docs/api-key

Google's September 2026 docs are phasing out old unrestricted standard keys.

## Live research

Fresh/current title expected:

```text
LIVE_RESEARCH|enabled
```

Evergreen expected:

```text
LIVE_RESEARCH|skip|evergreen
```

A grounding failure should fall back to normal script generation instead of killing the whole job.

## YouTube `redirect_uri_mismatch`

Check the exact callback URL in Google Cloud against ATTA's callback. HTTPS, hostname, path and trailing slash must match.

## Preset not included in backup

Check:

```bash
ls -lh backend/data/video_presets.json
cat backend/data/video_presets.json
```

## Backup verification

```bash
sha256sum -c backups/ARCHIVE.tar.gz.sha256
tar -tzf backups/ARCHIVE.tar.gz
```

## Tests

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests
```

Mocked/offline provider warnings can appear in tests; the important result is the final test status.
