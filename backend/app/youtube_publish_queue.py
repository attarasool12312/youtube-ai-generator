from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    VideoJob,
    YouTubeConnection,
    YouTubePublishJob,
)
from app.youtube_publish_files import (
    YouTubePublishFileError,
    resolve_final_video_path,
)
from app.youtube_publish_launcher import (
    start_youtube_publish_worker,
)


class YouTubePublishQueueError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def queue_youtube_publish_job(
    db: Session,
    publish_job: YouTubePublishJob,
) -> YouTubePublishJob:
    if publish_job.youtube_video_id:
        raise YouTubePublishQueueError(
            "already_uploaded",
            "This video has already been uploaded.",
        )

    if publish_job.status in {"queued", "uploading"}:
        raise YouTubePublishQueueError(
            "already_queued",
            "This upload is already queued or running.",
        )

    if (
        publish_job.status == "needs_review"
        and not publish_job.encrypted_upload_session_uri
    ):
        raise YouTubePublishQueueError(
            "manual_review_required",
            "This upload requires manual review before retrying.",
        )

    if publish_job.status not in {
        "draft",
        "failed",
        "needs_review",
    }:
        raise YouTubePublishQueueError(
            "invalid_publish_status",
            "This publishing job cannot currently be queued.",
        )

    video_job = db.get(
        VideoJob,
        publish_job.video_job_id,
    )
    connection = db.get(
        YouTubeConnection,
        publish_job.youtube_connection_id,
    )

    if video_job is None:
        raise YouTubePublishQueueError(
            "video_not_found",
            "The source video job no longer exists.",
        )

    if (
        connection is None
        or not connection.is_active
        or not connection.encrypted_refresh_token
    ):
        raise YouTubePublishQueueError(
            "channel_unavailable",
            "The selected YouTube channel is unavailable.",
        )

    try:
        resolve_final_video_path(video_job)
    except YouTubePublishFileError as error:
        raise YouTubePublishQueueError(
            "video_not_ready",
            str(error),
        ) from error

    resume_pending = bool(
        publish_job.encrypted_upload_session_uri
    )

    publish_job.status = "queued"

    if resume_pending:
        if publish_job.total_bytes:
            publish_job.progress = max(
                0,
                min(
                    99,
                    int(
                        publish_job.bytes_uploaded
                        * 100
                        / publish_job.total_bytes
                    ),
                ),
            )
    else:
        publish_job.progress = 0
        publish_job.bytes_uploaded = 0
        publish_job.encrypted_upload_session_uri = None

    publish_job.last_error = None
    publish_job.started_at = None
    publish_job.completed_at = None

    db.commit()
    db.refresh(publish_job)

    start_youtube_publish_worker()

    return publish_job
