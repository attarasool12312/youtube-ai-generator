import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select

from app.database import SessionLocal
from app.models import VideoJob

PROJECT_ROOT = Path(os.getenv(
    "YOUTUBE_AI_PROJECT_ROOT",
    str(Path(__file__).resolve().parents[2]),
)).resolve()
PID_FILE = PROJECT_ROOT / "backend/data/queue_worker.pid"
LOG_DIR = PROJECT_ROOT / "logs"


def get_next_job_id():
    with SessionLocal() as db:
        job = db.scalar(
            select(VideoJob)
            .where(VideoJob.status == "waiting")
            .order_by(VideoJob.queue_position, VideoJob.id)
            .limit(1)
        )
        return job.id if job else None


def process_job(job_id):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = (LOG_DIR / f"job-{job_id}.log").open("a")
    process = subprocess.Popen(
        [str(PROJECT_ROOT / ".venv/bin/python"),
         "-m", "app.job_runner", str(job_id)],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": "backend"},
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    with SessionLocal() as db:
        job = db.get(VideoJob, job_id)
        if job:
            job.worker_pid = process.pid
            job.status = "script_generation"
            job.progress = 2
            job.started_at = datetime.now(timezone.utc)
            job.completed_at = None
            job.error_message = None
            db.commit()

    return_code = process.wait()
    log.close()

    with SessionLocal() as db:
        job = db.get(VideoJob, job_id)
        if job:
            job.worker_pid = None

            if return_code != 0 and job.status != "cancelled":
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)

                if not job.error_message:
                    job.error_message = (
                        f"Job worker stopped with exit code "
                        f"{return_code}."
                    )

            db.commit()


def main():
    try:
        while True:
            job_id = get_next_job_id()
            if job_id is None:
                break
            process_job(job_id)
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
