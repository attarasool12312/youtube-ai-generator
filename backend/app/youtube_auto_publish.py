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
    "z-image-turbo",
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


def _selected_connection_ids(video_job: VideoJob) -> list[int]:
    result = []

    for raw in str(
        video_job.auto_publish_connection_ids or ""
    ).split(","):
        raw = raw.strip()

        if not raw:
            continue

        try:
            value = int(raw)
        except ValueError:
            continue

        if value > 0 and value not in result:
            result.append(value)

    # Backward compatibility with older single-channel jobs.
    if (
        not result
        and video_job.auto_publish_connection_id
    ):
        result.append(
            int(video_job.auto_publish_connection_id)
        )

    return result


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

        connection_ids = _selected_connection_ids(
            video_job
        )

        if not connection_ids:
            return "skipped_no_channel"

        connections = db.scalars(
            select(YouTubeConnection).where(
                YouTubeConnection.id.in_(
                    connection_ids
                )
            )
        ).all()

        by_id = {
            connection.id: connection
            for connection in connections
        }

        for connection_id in connection_ids:
            connection = by_id.get(connection_id)

            if (
                connection is None
                or not connection.is_active
                or not connection.encrypted_refresh_token
            ):
                raise RuntimeError(
                    "Automatic publishing channel "
                    f"{connection_id} is unavailable."
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
                title=(
                    video_job.title
                    or "Untitled Video"
                )[:100],
                description="",
                tags=[],
                category_id="27",
                language="en",
                provider="basic-no-ai-metadata",
                stock_credits="",
            )

        synthetic_media = (
            package_contains_synthetic_media(
                video_job
            )
        )

        results = []

        for connection_id in connection_ids:
            connection = by_id[connection_id]

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
                    results.append(
                        f"{connection.id}:already_uploaded"
                    )
                    continue

                if existing.status in {
                    "queued",
                    "uploading",
                    "uploaded",
                    "needs_review",
                }:
                    results.append(
                        f"{connection.id}:"
                        f"existing_{existing.status}"
                    )
                    continue

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
                scheduled_at=(
                    video_job.auto_publish_scheduled_at
                ),
                made_for_kids=bool(
                    video_job.auto_publish_made_for_kids
                ),
                contains_synthetic_media=synthetic_media,
                notify_subscribers=bool(
                    video_job.auto_publish_notify_subscribers
                ),
                stock_credits=metadata.stock_credits,
                thumbnail_path=(
                    video_job.manual_thumbnail_path
                    or None
                ),
            )

            if video_job.manual_thumbnail_path:
                draft.thumbnail_status = "not_started"
            else:
                draft.thumbnail_status = "not_requested"

            draft.thumbnail_error = None
            db.commit()
            db.refresh(draft)

            queue_youtube_publish_job(
                db,
                draft,
            )

            results.append(
                f"{connection.id}:queued_{draft.id}"
            )

        return (
            " | ".join(results)
            + f" | provider_{metadata.provider}"
        )
