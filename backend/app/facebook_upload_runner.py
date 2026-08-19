from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import (
    FacebookConnection,
    FacebookPublishJob,
    VideoJob,
)
from app.youtube_publish_files import resolve_final_video_path
from app.zernio_client import (
    ZernioError,
    create_facebook_video_post,
    create_media_upload,
    get_post,
    upload_presigned_file,
)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _facebook_platform_result(post: dict) -> dict:
    platforms = post.get("platforms") or []

    for item in platforms:
        if (
            isinstance(item, dict)
            and item.get("platform") == "facebook"
        ):
            return item

    return {}


def _save_remote_state(
    publish_job_id: int,
    post: dict,
) -> str:
    platform = _facebook_platform_result(post)

    remote_status = str(
        platform.get("status")
        or post.get("status")
        or ""
    ).lower()

    facebook_url = (
        platform.get("platformPostUrl")
        or post.get("platformPostUrl")
    )

    platform_post_id = (
        platform.get("platformPostId")
        or post.get("platformPostId")
    )

    with SessionLocal() as db:
        publish_job = db.get(
            FacebookPublishJob,
            publish_job_id,
        )

        if publish_job is None:
            return remote_status

        if post.get("_id"):
            publish_job.zernio_post_id = str(
                post["_id"]
            )

        if platform_post_id:
            publish_job.platform_post_id = str(
                platform_post_id
            )

        if facebook_url:
            publish_job.facebook_url = str(
                facebook_url
            )

        if remote_status == "published":
            publish_job.status = "published"
            publish_job.progress = 100
            publish_job.completed_at = _utc_now_naive()

        elif remote_status == "scheduled":
            publish_job.status = "scheduled"
            publish_job.progress = 100
            publish_job.completed_at = _utc_now_naive()

        elif remote_status in {
            "failed",
            "cancelled",
        }:
            publish_job.status = "failed"
            publish_job.last_error = (
                platform.get("error")
                or post.get("error")
                or "Facebook publishing failed."
            )
            publish_job.completed_at = _utc_now_naive()

        else:
            publish_job.status = "publishing"
            publish_job.progress = max(
                publish_job.progress or 0,
                99,
            )

        db.commit()

    return remote_status


def _resume_existing_post(
    publish_job_id: int,
) -> bool:
    with SessionLocal() as db:
        publish_job = db.get(
            FacebookPublishJob,
            publish_job_id,
        )

        if (
            publish_job is None
            or not publish_job.zernio_post_id
        ):
            return False

        post_id = publish_job.zernio_post_id

    post = get_post(post_id)
    status = _save_remote_state(
        publish_job_id,
        post,
    )

    return status in {
        "published",
        "scheduled",
        "failed",
        "cancelled",
    }


def run_publish_job(
    publish_job_id: int,
) -> None:
    # If a previous process created the Zernio post but was
    # interrupted afterwards, recover it instead of creating
    # a duplicate post.
    if _resume_existing_post(publish_job_id):
        return

    with SessionLocal() as db:
        publish_job = db.get(
            FacebookPublishJob,
            publish_job_id,
        )

        if publish_job is None:
            raise RuntimeError(
                "Facebook publishing job not found."
            )

        if publish_job.status != "queued":
            raise RuntimeError(
                "Facebook publishing job is not queued."
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
            raise RuntimeError(
                "Source video job not found."
            )

        if video_job.status != "completed":
            raise RuntimeError(
                "Source video is not completed."
            )

        if (
            connection is None
            or not connection.is_active
            or connection.user_id != publish_job.user_id
        ):
            raise RuntimeError(
                "Facebook Page is unavailable."
            )

        video_path = resolve_final_video_path(
            video_job
        )
        total_bytes = video_path.stat().st_size

        if total_bytes <= 0:
            raise RuntimeError(
                "Final video file is empty."
            )

        publish_job.status = "uploading"
        publish_job.progress = 1
        publish_job.bytes_uploaded = 0
        publish_job.total_bytes = total_bytes
        publish_job.attempt_count = (
            publish_job.attempt_count or 0
        ) + 1
        publish_job.last_error = None
        publish_job.started_at = _utc_now_naive()
        publish_job.completed_at = None

        db.commit()

        account_id = connection.zernio_account_id
        title = publish_job.title or video_job.title or "Video"
        description = publish_job.description or ""
        scheduled_at = publish_job.scheduled_at
        request_id = publish_job.request_id

    presigned = create_media_upload(
        video_path.name,
        "video/mp4",
        total_bytes,
    )

    upload_url = presigned.get("uploadUrl")
    public_url = presigned.get("publicUrl")

    if not upload_url or not public_url:
        raise RuntimeError(
            "Zernio did not return media upload URLs."
        )

    progress_state = {
        "last_bytes": 0,
        "last_time": 0.0,
    }

    def update_upload_progress(
        uploaded: int,
        total: int,
    ) -> None:
        now = time.monotonic()

        should_update = (
            uploaded >= total
            or uploaded - progress_state["last_bytes"]
            >= 8 * 1024 * 1024
            or now - progress_state["last_time"] >= 2.0
        )

        if not should_update:
            return

        progress_state["last_bytes"] = uploaded
        progress_state["last_time"] = now

        progress = min(
            95,
            max(
                1,
                int(uploaded * 95 / total),
            ),
        )

        with SessionLocal() as db:
            job = db.get(
                FacebookPublishJob,
                publish_job_id,
            )

            if job is None:
                return

            job.bytes_uploaded = uploaded
            job.total_bytes = total
            job.progress = progress
            db.commit()

    upload_presigned_file(
        upload_url,
        video_path,
        content_type="video/mp4",
        progress_callback=update_upload_progress,
    )

    with SessionLocal() as db:
        publish_job = db.get(
            FacebookPublishJob,
            publish_job_id,
        )

        if publish_job is None:
            raise RuntimeError(
                "Facebook publishing job disappeared."
            )

        publish_job.status = "publishing"
        publish_job.progress = 97
        publish_job.bytes_uploaded = total_bytes
        publish_job.total_bytes = total_bytes
        db.commit()

    try:
        post = create_facebook_video_post(
            account_id=account_id,
            media_url=str(public_url),
            content=description,
            title=title,
            scheduled_at=scheduled_at,
            request_id=request_id,
        )

    except ZernioError as error:
        existing_post_id = (
            error.details.get("existingPostId")
            if isinstance(error.details, dict)
            else None
        )

        if not existing_post_id:
            raise

        post = get_post(
            str(existing_post_id)
        )

    post_id = post.get("_id")

    if not post_id:
        raise RuntimeError(
            "Zernio returned no post ID."
        )

    with SessionLocal() as db:
        publish_job = db.get(
            FacebookPublishJob,
            publish_job_id,
        )

        if publish_job is None:
            raise RuntimeError(
                "Facebook publishing job disappeared."
            )

        publish_job.zernio_post_id = str(post_id)
        db.commit()

    status = _save_remote_state(
        publish_job_id,
        post,
    )

    if status in {
        "published",
        "scheduled",
        "failed",
        "cancelled",
    }:
        return

    # Immediate Facebook video publishing can spend some
    # time processing after Zernio accepts the request.
    # Poll without blocking video generation itself.
    deadline = time.monotonic() + 900

    while time.monotonic() < deadline:
        time.sleep(10)

        post = get_post(str(post_id))
        status = _save_remote_state(
            publish_job_id,
            post,
        )

        if status in {
            "published",
            "scheduled",
            "failed",
            "cancelled",
        }:
            return

    # Do not mark it failed merely because Facebook is still
    # processing. The Publishing page can continue showing it
    # as publishing.
    with SessionLocal() as db:
        publish_job = db.get(
            FacebookPublishJob,
            publish_job_id,
        )

        if publish_job is not None:
            publish_job.status = "publishing"
            publish_job.progress = 99
            db.commit()


def main() -> None:
    publish_job_id = int(sys.argv[1])

    try:
        run_publish_job(
            publish_job_id,
        )

    except Exception as error:
        with SessionLocal() as db:
            publish_job = db.get(
                FacebookPublishJob,
                publish_job_id,
            )

            if publish_job is not None:
                publish_job.status = "failed"
                publish_job.last_error = str(error)
                publish_job.completed_at = _utc_now_naive()
                db.commit()

        print(
            "FACEBOOK_PUBLISH_FAILED:",
            str(error),
            file=sys.stderr,
            flush=True,
        )
        raise


if __name__ == "__main__":
    main()
