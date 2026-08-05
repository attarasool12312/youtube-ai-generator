import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import redis

from app.api_settings import has_api_key
from app.config import settings
from app.paths import OUTPUT_ROOT


_last_cpu_sample = None


def _percent(value, total):
    if not total:
        return 0.0
    return round((value / total) * 100, 1)


def _format_bytes(value):
    value = float(max(0, value))
    units = ("B", "KB", "MB", "GB", "TB")

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{value:.1f} TB"


def _read_cpu_times():
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [int(value) for value in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        return idle, total
    except Exception:
        return None


def cpu_stats():
    global _last_cpu_sample

    current = _read_cpu_times()
    usage = None

    if current and _last_cpu_sample:
        idle_delta = current[0] - _last_cpu_sample[0]
        total_delta = current[1] - _last_cpu_sample[1]

        if total_delta > 0:
            usage = round(
                max(0.0, min(100.0, 100 * (1 - idle_delta / total_delta))),
                1,
            )

    _last_cpu_sample = current

    load_1, load_5, load_15 = os.getloadavg()

    return {
        "usage_percent": usage,
        "cores": os.cpu_count() or 1,
        "load_1": round(load_1, 2),
        "load_5": round(load_5, 2),
        "load_15": round(load_15, 2),
    }


def memory_stats():
    values = {}

    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except Exception:
        values = {}

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(0, total - available)

    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used": _format_bytes(used),
        "total": _format_bytes(total),
        "usage_percent": _percent(used, total),
    }


def storage_stats():
    target = OUTPUT_ROOT if OUTPUT_ROOT.exists() else Path("/")
    usage = shutil.disk_usage(target)

    return {
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used": _format_bytes(usage.used),
        "free": _format_bytes(usage.free),
        "total": _format_bytes(usage.total),
        "usage_percent": _percent(usage.used, usage.total),
    }


def gpu_stats():
    command = [
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )

        line = result.stdout.strip().splitlines()[0]
        name, utilization, memory_used, memory_total, temperature = [
            item.strip() for item in line.split(",", 4)
        ]

        memory_used_mb = float(memory_used)
        memory_total_mb = float(memory_total)

        return {
            "online": True,
            "name": name,
            "usage_percent": float(utilization),
            "memory_used": f"{memory_used_mb / 1024:.1f} GB",
            "memory_total": f"{memory_total_mb / 1024:.1f} GB",
            "memory_percent": _percent(memory_used_mb, memory_total_mb),
            "temperature": f"{temperature}°C",
        }
    except Exception:
        return {
            "online": False,
            "name": "GPU unavailable",
            "usage_percent": 0,
            "memory_used": "0 GB",
            "memory_total": "0 GB",
            "memory_percent": 0,
            "temperature": "N/A",
        }


def redis_status():
    try:
        client = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )

        started = time.perf_counter()
        online = bool(client.ping())
        latency = round((time.perf_counter() - started) * 1000)

        return {
            "online": online,
            "label": "Connected" if online else "Unavailable",
            "detail": f"{latency} ms",
        }
    except Exception:
        return {
            "online": False,
            "label": "Unavailable",
            "detail": "Connection failed",
        }


def ollama_status():
    try:
        started = time.perf_counter()
        response = httpx.get(
            f"{settings.ollama_url.rstrip('/')}/api/tags",
            timeout=2,
        )
        response.raise_for_status()

        models = [
            item.get("name", "")
            for item in response.json().get("models", [])
            if item.get("name")
        ]

        latency = round((time.perf_counter() - started) * 1000)

        return {
            "online": True,
            "label": "Connected",
            "detail": f"{len(models)} model(s) · {latency} ms",
            "models": models,
        }
    except Exception:
        return {
            "online": False,
            "label": "Unavailable",
            "detail": "Connection failed",
            "models": [],
        }


def api_connections():
    providers = {
        "Gemini": has_api_key("gemini_api_key"),
        "Together AI": has_api_key("together_api_key"),
        "Pexels": has_api_key("pexels_api_key"),
        "Pixabay": has_api_key("pixabay_api_key"),
        "Coverr": has_api_key("coverr_api_key"),
        "Openverse": (
            has_api_key("openverse_client_id")
            and has_api_key("openverse_client_secret")
        ),
    }

    return {
        "providers": providers,
        "connected": sum(1 for ready in providers.values() if ready),
        "total": len(providers),
    }


def collect_dashboard_metrics():
    return {
        "cpu": cpu_stats(),
        "memory": memory_stats(),
        "storage": storage_stats(),
        "gpu": gpu_stats(),
        "redis": redis_status(),
        "ollama": ollama_status(),
        "apis": api_connections(),
        "updated_at": int(time.time()),
    }
