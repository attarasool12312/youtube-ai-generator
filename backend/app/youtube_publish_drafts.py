from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    VideoJob,
    YouTubeConnection,
    YouTubePublishJob,
)
from app.youtube_publish_files import resolve_final_video_path
from app.youtube_publish_validation import (
    YouTubePublishValidationError,
    validate_youtube_metadata,
)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_youtube_publish_draft(
    db: Session,
    *,
    video_job: VideoJob,
    connection: YouTubeConnection,
    title: str,
    description: str,
    tags: str,
    category_id: str,
    language: str,
    privacy_status: str,
    made_for_kids: bool,
    contains_synthetic_media: bool | None,
    notify_subscribers: bool,
    scheduled_at: datetime | None = None,
    stock_credits: str | None = None,
    thumbnail_path: str | None = None,
) -> YouTubePublishJob:
    if not connection.is_active:
        raise YouTubePublishValidationError([
            "The selected YouTube channel is disconnected."
        ])

    if not connection.encrypted_refresh_token:
        raise YouTubePublishValidationError([
            "The selected channel has no valid OAuth refresh token."
        ])

    video_path = resolve_final_video_path(video_job)

    metadata = validate_youtube_metadata(
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        language=language,
        privacy_status=privacy_status,
    )

    if scheduled_at is not None:
        if metadata.privacy_status != "private":
            raise YouTubePublishValidationError([
                "Scheduled videos must initially use private privacy."
            ])

        if scheduled_at <= utc_now_naive():
            raise YouTubePublishValidationError([
                "Scheduled publication time must be in the future."
            ])

    draft = db.scalar(
        select(YouTubePublishJob).where(
            YouTubePublishJob.video_job_id == video_job.id,
            YouTubePublishJob.youtube_connection_id
            == connection.id,
        )
    )

    if draft and draft.youtube_video_id:
        raise YouTubePublishValidationError([
            "This video has already been uploaded to this channel."
        ])

    if draft is None:
        draft = YouTubePublishJob(
            video_job_id=video_job.id,
            youtube_connection_id=connection.id,
            title=metadata.title,
        )
        db.add(draft)

    draft.status = "draft"
    draft.progress = 0
    draft.title = metadata.title
    draft.description = metadata.description
    draft.tags = json.dumps(
        metadata.tags,
        ensure_ascii=False,
    )
    draft.category_id = metadata.category_id
    draft.language = metadata.language
    draft.privacy_status = metadata.privacy_status
    draft.made_for_kids = made_for_kids
    draft.contains_synthetic_media = (
        contains_synthetic_media
    )
    draft.notify_subscribers = notify_subscribers
    draft.scheduled_at = scheduled_at
    draft.stock_credits = stock_credits
    draft.thumbnail_path = thumbnail_path
    draft.total_bytes = video_path.stat().st_size
    draft.bytes_uploaded = 0
    draft.last_error = None

    db.commit()
    db.refresh(draft)

    return draft
