"""
GPU resource detection and management.

Detects available VRAM at runtime and provides resource allocation
decisions. On 16GB GPUs, runs conservative. On 24GB+, runs aggressive.
Everything auto-adjusts based on what hardware is available.
"""

import subprocess
import logging

logger = logging.getLogger(__name__)

_cached_info = None


def get_gpu_info():
    global _cached_info
    if _cached_info is not None:
        return _cached_info
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            line = result.stdout.strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            _cached_info = {"name": parts[0], "vram_mb": int(parts[1])}
            logger.info("GPU: %s, %d MB VRAM", parts[0], int(parts[1]))
            return _cached_info
    except Exception as e:
        logger.warning("GPU detection failed: %s", e)
    _cached_info = {"name": "unknown", "vram_mb": 0}
    return _cached_info


def get_vram_mb():
    return get_gpu_info()["vram_mb"]


def get_gpu_name():
    return get_gpu_info()["name"]


def can_parallel_render_avatar():
    """NVENC ~500MB + MuseTalk ~8GB = ~8.5GB. Safe on 12GB+."""
    return get_vram_mb() >= 12000


def log_gpu_status():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            logger.info("GPU: %s | VRAM: %s/%s MB | Util: %s%%",
                        parts[0], parts[1], parts[2], parts[3])
    except Exception:
        pass
