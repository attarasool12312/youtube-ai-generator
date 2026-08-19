from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import (
    FacebookConnection,
    FacebookPublishJob,
    VideoJob,
)
from app.youtube_publish_files import (
    YouTubePublishFileError,
    resolve_final_video_path,
)
from app.facebook_publish_launcher import (
    start_facebook_publish_worker,
)


class FacebookPublishQueueError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def queue_facebook_publish_job(
    db: Session,
    publish_job: FacebookPublishJob,
) -> FacebookPublishJob:
    if (
        publish_job.zernio_post_id
        and publish_job.status in {
            "published",
            "scheduled",
        }
    ):
        raise FacebookPublishQueueError(
            "already_published",
            "This Facebook publishing job has already been submitted.",
        )

    if publish_job.status in {
        "queued",
        "uploading",
        "publishing",
    }:
        raise FacebookPublishQueueError(
            "already_queued",
            "This Facebook publishing job is already running.",
        )

    if publish_job.status not in {
        "draft",
        "failed",
    }:
        raise FacebookPublishQueueError(
            "invalid_publish_status",
            "This Facebook publishing job cannot currently be queued.",
        )

    video_job = db.get(
        VideoJob,
        publish_job.video_job_id,
    )
    connection = db.get(
        FacebookConnection,
        publish_job.facebook_connection_id,
    )

    if video_job is None:
        raise FacebookPublishQueueError(
            "video_not_found",
            "The source video no longer exists.",
        )

    if video_job.status != "completed":
        raise FacebookPublishQueueError(
            "video_not_ready",
            "The source video is not completed.",
        )

    if (
        connection is None
        or not connection.is_active
        or connection.user_id != publish_job.user_id
    ):
        raise FacebookPublishQueueError(
            "page_unavailable",
            "The selected Facebook Page is unavailable.",
        )

    try:
        resolve_final_video_path(video_job)
    except YouTubePublishFileError as error:
        raise FacebookPublishQueueError(
            "video_not_ready",
            str(error),
        ) from error

    publish_job.status = "queued"
    publish_job.progress = 0
    publish_job.bytes_uploaded = 0
    publish_job.total_bytes = 0
    publish_job.last_error = None
    publish_job.started_at = None
    publish_job.completed_at = None

    if not publish_job.request_id:
        publish_job.request_id = str(uuid.uuid4())

    db.commit()
    db.refresh(publish_job)

    start_facebook_publish_worker()

    return publish_job
