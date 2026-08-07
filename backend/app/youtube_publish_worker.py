from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import YouTubePublishJob


PROJECT_ROOT = Path(
    os.getenv(
        "YOUTUBE_AI_PROJECT_ROOT",
        str(Path(__file__).resolve().parents[2]),
    )
).resolve()

PID_FILE = (
    PROJECT_ROOT
    / "backend/data/youtube_publish_worker.pid"
)

LOG_DIRECTORY = PROJECT_ROOT / "logs/youtube-publishing"


def get_next_publish_job_id() -> int | None:
    with SessionLocal() as db:
        publish_job = db.scalar(
            select(YouTubePublishJob)
            .where(YouTubePublishJob.status == "queued")
            .order_by(YouTubePublishJob.id)
            .limit(1)
        )

        return publish_job.id if publish_job else None


def process_publish_job(publish_job_id: int) -> None:
    LOG_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_path = (
        LOG_DIRECTORY
        / f"publish-{publish_job_id}.log"
    )

    with log_path.open("a") as log:
        process = subprocess.Popen(
            [
                str(PROJECT_ROOT / ".venv/bin/python"),
                "-m",
                "app.youtube_upload_runner",
                str(publish_job_id),
            ],
            cwd=str(PROJECT_ROOT),
            env={
                **os.environ,
                "PYTHONPATH": "backend",
            },
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        return_code = process.wait()

    if return_code == 0:
        return

    with SessionLocal() as db:
        publish_job = db.get(
            YouTubePublishJob,
            publish_job_id,
        )

        if publish_job is None:
            return

        if publish_job.status not in {
            "failed",
            "needs_review",
            "uploaded",
        }:
            publish_job.status = "failed"

            if not publish_job.last_error:
                publish_job.last_error = (
                    "Publishing worker stopped with exit code "
                    f"{return_code}."
                )

            db.commit()


def recover_interrupted_publish_jobs() -> None:
    with SessionLocal() as db:
        interrupted_jobs = db.scalars(
            select(YouTubePublishJob)
            .where(
                YouTubePublishJob.status == "uploading"
            )
            .order_by(YouTubePublishJob.id)
        ).all()

        if not interrupted_jobs:
            return

        for publish_job in interrupted_jobs:
            if publish_job.youtube_video_id:
                publish_job.status = "uploaded"
                publish_job.progress = 100
                continue

            if publish_job.encrypted_upload_session_uri:
                publish_job.status = "queued"
                publish_job.last_error = (
                    "Recovered interrupted YouTube upload. "
                    "The stored resumable session will be resumed."
                )
            else:
                publish_job.status = "needs_review"
                publish_job.last_error = (
                    "The YouTube upload was interrupted before "
                    "a resumable session was safely stored. "
                    "Manual review is required to avoid a "
                    "possible duplicate upload."
                )

        db.commit()


def main() -> None:
    try:
        recover_interrupted_publish_jobs()

        while True:
            publish_job_id = get_next_publish_job_id()

            if publish_job_id is None:
                break

            process_publish_job(publish_job_id)

    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
