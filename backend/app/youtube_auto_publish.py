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
    generate_youtube_metadata,
)
from app.youtube_publish_drafts import (
    save_youtube_publish_draft,
)
from app.youtube_thumbnail_generator import (
    generate_youtube_thumbnail,
)
from app.youtube_publish_queue import (
    queue_youtube_publish_job,
)


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

        if not video_job.auto_publish_metadata:
            return "skipped_metadata_disabled"

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

        metadata = generate_youtube_metadata(
            video_job
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

        if video_job.auto_publish_thumbnail:
            draft.status = "thumbnail_generation"
            draft.thumbnail_status = "generating"
            draft.thumbnail_error = None
            db.commit()
            db.refresh(draft)

            try:
                thumbnail = generate_youtube_thumbnail(
                    video_job,
                    publish_title=metadata.title,
                )

                draft.thumbnail_path = thumbnail.path
                draft.thumbnail_status = "ready"
                draft.thumbnail_error = None

            except Exception as error:
                draft.thumbnail_path = None
                draft.thumbnail_status = "failed"
                draft.thumbnail_error = str(error)[:4000]

            draft.status = "draft"
            db.commit()
            db.refresh(draft)

        else:
            draft.thumbnail_status = "not_requested"
            draft.thumbnail_error = None
            db.commit()
            db.refresh(draft)

        queue_youtube_publish_job(db, draft)

        return (
            f"queued_publish_job_{draft.id}"
            f"_provider_{metadata.provider}"
        )
