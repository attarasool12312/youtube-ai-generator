from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    VideoJob,
    YouTubeConnection,
    YouTubePublishJob,
)
from app.youtube_metadata_generator import (
    GeneratedYouTubeMetadata,
    generate_youtube_metadata,
)
from app.youtube_publish_drafts import (
    save_youtube_publish_draft,
)
from app.youtube_publish_queue import (
    queue_youtube_publish_job,
)


def _saved_youtube_metadata(video_job):
    if (
        not video_job.youtube_title
        or not video_job.youtube_description
    ):
        return None

    tags = [
        tag.strip()
        for tag in (video_job.youtube_tags or "").split(",")
        if tag.strip()
    ]

    return GeneratedYouTubeMetadata(
        title=video_job.youtube_title,
        description=video_job.youtube_description,
        tags=tags,
        category_id=video_job.youtube_category_id or "27",
        language=video_job.youtube_language or "en",
        provider=(
            video_job.youtube_metadata_provider
            or "saved-post-render"
        ),
        stock_credits=video_job.youtube_stock_credits or "",
    )


def _store_youtube_metadata(video_job, metadata):
    video_job.youtube_title = metadata.title
    video_job.youtube_description = metadata.description
    video_job.youtube_tags = ", ".join(metadata.tags)
    video_job.youtube_category_id = metadata.category_id
    video_job.youtube_language = metadata.language
    video_job.youtube_metadata_provider = metadata.provider
    video_job.youtube_stock_credits = metadata.stock_credits


AI_MEDIA_PROVIDERS = {
    "ai",
    "flux",
    "generated",
    "openai",
    "stability",
    "local_ai",
}


def package_contains_synthetic_media(
    video_job: VideoJob,
) -> bool:
    if video_job.media_source == "ai_only":
        return True

    package_path = (
        Path(video_job.output_directory)
        / "package.json"
    )

    if not package_path.is_file():
        return False

    package = json.loads(
        package_path.read_text(encoding="utf-8")
    )

    for scene in package.get("scenes", []):
        providers = {
            str(
                scene.get("provider") or ""
            ).strip().lower(),
            str(
                scene.get("media_provider") or ""
            ).strip().lower(),
        }

        if providers & AI_MEDIA_PROVIDERS:
            return True

    return False


def prepare_and_queue_auto_publish(
    video_job_id: int,
) -> str:
    with SessionLocal() as db:
        video_job = db.get(VideoJob, video_job_id)

        if video_job is None:
            return "skipped_video_not_found"

        if video_job.status != "completed":
            return "skipped_video_not_completed"

        if not video_job.auto_publish_enabled:
            return "skipped_disabled"

        if not video_job.auto_publish_connection_id:
            return "skipped_no_channel"

        connection = db.get(
            YouTubeConnection,
            video_job.auto_publish_connection_id,
        )

        if (
            connection is None
            or not connection.is_active
            or not connection.encrypted_refresh_token
        ):
            raise RuntimeError(
                "Automatic publishing channel is unavailable."
            )

        existing = db.scalar(
            select(YouTubePublishJob).where(
                YouTubePublishJob.video_job_id
                == video_job.id,
                YouTubePublishJob.youtube_connection_id
                == connection.id,
            )
        )

        if existing is not None:
            if existing.youtube_video_id:
                return "skipped_already_uploaded"

            if existing.status in {
                "queued",
                "uploading",
                "uploaded",
                "needs_review",
            }:
                return (
                    "skipped_existing_"
                    + existing.status
                )

        if video_job.auto_publish_metadata:
            metadata = _saved_youtube_metadata(
                video_job
            )

            if metadata is None:
                metadata = generate_youtube_metadata(
                    video_job
                )
                _store_youtube_metadata(
                    video_job,
                    metadata,
                )
                db.commit()
                db.refresh(video_job)
        else:
            metadata = GeneratedYouTubeMetadata(
                title=(video_job.title or "Untitled Video")[:100],
                description="",
                tags=[],
                category_id="27",
                language="en",
                provider="basic-no-ai-metadata",
                stock_credits="",
            )

        draft = save_youtube_publish_draft(
            db,
            video_job=video_job,
            connection=connection,
            title=metadata.title,
            description=metadata.description,
            tags=", ".join(metadata.tags),
            category_id=metadata.category_id,
            language=metadata.language,
            privacy_status=(
                video_job.auto_publish_privacy
                or "private"
            ),
            scheduled_at=video_job.auto_publish_scheduled_at,
            made_for_kids=bool(
                video_job.auto_publish_made_for_kids
            ),
            contains_synthetic_media=(
                package_contains_synthetic_media(
                    video_job
                )
            ),
            notify_subscribers=bool(
                video_job.auto_publish_notify_subscribers
            ),
            stock_credits=metadata.stock_credits,
            thumbnail_path=None,
        )

        # Thumbnail image generation is intentionally disabled.
        # Every completed video receives a Gemini thumbnail prompt
        # independently in job_runner.py.
        draft.thumbnail_path = None
        draft.thumbnail_status = "not_requested"
        draft.thumbnail_error = None
        db.commit()
        db.refresh(draft)

        queue_youtube_publish_job(db, draft)

        return (
            f"queued_publish_job_{draft.id}"
            f"_provider_{metadata.provider}"
        )
