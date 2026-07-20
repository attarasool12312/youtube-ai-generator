import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(os.getenv(
    "YOUTUBE_AI_PROJECT_ROOT",
    str(Path(__file__).resolve().parents[2]),
)).resolve()
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

    with LOG_FILE.open("a") as log:
        process = subprocess.Popen(
            [
                str(PROJECT_ROOT / ".venv/bin/python"),
                "-m",
                "app.queue_worker",
            ],
            cwd=str(PROJECT_ROOT),
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    PID_FILE.write_text(str(process.pid))
    return True
