from __future__ import annotations

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    FacebookConnection,
    FacebookPublishJob,
    VideoJob,
)
from app.facebook_publish_queue import (
    queue_facebook_publish_job,
)


def prepare_and_queue_facebook_auto_publish(
    video_job_id: int,
) -> str:
    with SessionLocal() as db:
        video_job = db.get(VideoJob, video_job_id)

        if video_job is None:
            return "skipped_video_not_found"

        if video_job.status != "completed":
            return "skipped_video_not_completed"

        if not video_job.facebook_auto_publish_enabled:
            return "skipped_disabled"

        if not video_job.facebook_auto_publish_connection_id:
            return "skipped_no_page"

        connection = db.get(
            FacebookConnection,
            video_job.facebook_auto_publish_connection_id,
        )

        if connection is None or not connection.is_active:
            raise RuntimeError(
                "Automatic Facebook publishing Page is unavailable."
            )

        existing = db.scalar(
            select(FacebookPublishJob).where(
                FacebookPublishJob.video_job_id
                == video_job.id,
                FacebookPublishJob.facebook_connection_id
                == connection.id,
            )
        )

        if existing is not None:
            if existing.status in {
                "queued",
                "uploading",
                "publishing",
                "scheduled",
                "published",
            }:
                return (
                    "skipped_existing_"
                    + existing.status
                )

            publish_job = existing
        else:
            publish_job = FacebookPublishJob(
                user_id=connection.user_id,
                video_job_id=video_job.id,
                facebook_connection_id=connection.id,
                status="draft",
            )
            db.add(publish_job)

        publish_job.title = (
            video_job.youtube_title
            or video_job.title
            or "Video"
        )[:255]

        publish_job.description = (
            video_job.youtube_description
            or ""
        )

        publish_job.scheduled_at = (
            video_job.facebook_auto_publish_scheduled_at
        )

        db.commit()
        db.refresh(publish_job)

        queue_facebook_publish_job(
            db,
            publish_job,
        )

        return (
            f"queued_facebook_publish_job_{publish_job.id}"
        )
