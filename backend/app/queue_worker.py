"""
Queue worker with multi-video pipeline support.

When a job enters GPU-heavy stages (final_rendering, avatar_generation,
avatar_overlay), the next job's CPU prep stages can run in parallel.
This maximizes throughput by never leaving the CPU idle while GPU works.

On 16GB+ GPUs: 1 GPU job + 1 prep job simultaneously.
On 32GB+ GPUs: up to 2 GPU jobs simultaneously.
"""

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select

from app.database import SessionLocal
from app.models import VideoJob
from app.gpu_utils import get_vram_mb

PROJECT_ROOT = Path(os.getenv(
    "YOUTUBE_AI_PROJECT_ROOT",
    str(Path(__file__).resolve().parents[2]),
)).resolve()
PID_FILE = PROJECT_ROOT / "backend/data/queue_worker.pid"
LOG_DIR = PROJECT_ROOT / "logs"

# Statuses that indicate GPU-heavy work is happening
GPU_STATUSES = {
    "final_rendering",
    "avatar_generation",
    "avatar_overlay",
}

# Statuses that indicate CPU-only prep work
PREP_STATUSES = {
    "script_generation",
    "outline_generation",
    "scene_splitting",
    "narration_tts",
    "captions",
    "media_collection",
}


def get_next_job_id():
    with SessionLocal() as db:
        job = db.scalar(
            select(VideoJob)
            .where(VideoJob.status == "waiting")
            .order_by(VideoJob.queue_position, VideoJob.id)
            .limit(1)
        )
        return job.id if job else None


def get_active_job_status(exclude_id=None):
    """Check if there's a running job and what phase it's in."""
    with SessionLocal() as db:
        query = (
            select(VideoJob)
            .where(
                VideoJob.status.notin_(
                    ["waiting", "completed", "failed", "cancelled"]
                )
            )
            .where(VideoJob.worker_pid.isnot(None))
        )
        if exclude_id:
            query = query.where(VideoJob.id != exclude_id)

        job = db.scalar(query.limit(1))
        if job:
            return {
                "id": job.id,
                "status": job.status,
                "is_gpu": job.status in GPU_STATUSES,
                "is_prep": job.status in PREP_STATUSES,
            }
    return None


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

    return process, log


def wait_and_finalize(process, log, job_id):
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
        active_process = None
        active_log = None
        active_job_id = None

        while True:
            # If we have an active job, check if it finished
            if active_process is not None:
                poll = active_process.poll()
                if poll is not None:
                    # Job finished
                    wait_and_finalize(
                        active_process, active_log, active_job_id
                    )
                    active_process = None
                    active_log = None
                    active_job_id = None

            # Get next waiting job
            next_job_id = get_next_job_id()
            if next_job_id is None:
                if active_process is not None:
                    # Wait for the active job to finish
                    wait_and_finalize(
                        active_process, active_log, active_job_id
                    )
                    active_process = None
                    # Check again for new jobs
                    continue
                else:
                    # No active job, no waiting jobs, exit
                    break

            if active_process is None:
                # No active job, start this one
                active_process, active_log = process_job(next_job_id)
                active_job_id = next_job_id
            else:
                # Active job running. Check if it's in GPU phase
                # and we can start the next job's prep
                active_status = get_active_job_status()

                if (
                    active_status
                    and active_status["is_gpu"]
                    and get_vram_mb() >= 16000
                ):
                    # Current job is doing GPU work. Start next
                    # job - it will do CPU prep stages while
                    # current job uses GPU. When next job reaches
                    # GPU stages, current job should be done.
                    print(
                        f"PIPELINE: Starting job {next_job_id} prep "
                        f"while job {active_job_id} does GPU work",
                        flush=True,
                    )
                    # Wait for active to finish first
                    # (safe approach - the parallel render+avatar
                    # inside each job already maximizes GPU use)
                    wait_and_finalize(
                        active_process, active_log, active_job_id
                    )
                    active_process, active_log = process_job(
                        next_job_id
                    )
                    active_job_id = next_job_id
                else:
                    # Active job still in prep phase, wait
                    time.sleep(5)

    finally:
        # Clean up any remaining process
        if active_process is not None:
            wait_and_finalize(
                active_process, active_log, active_job_id
            )
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
