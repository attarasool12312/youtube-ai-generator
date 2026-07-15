import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path("/workspace/youtube-ai-generator")
PID_FILE = PROJECT_ROOT / "backend/data/queue_worker.pid"
LOG_FILE = PROJECT_ROOT / "logs/queue_worker.log"


def worker_is_running() -> bool:
    if not PID_FILE.exists():
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return False


def start_queue_worker() -> bool:
    if worker_is_running():
        return False

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = "backend"


def start_queue_worker() -> bool:
    if worker_is_running():
        return False

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = "backend"

    PID_FILE.write_text(str(process.pid))
    return True
