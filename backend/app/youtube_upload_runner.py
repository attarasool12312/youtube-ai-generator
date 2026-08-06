from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.api_settings import encrypt_value
from app.database import SessionLocal
from app.models import (
    VideoJob,
    YouTubeConnection,
    YouTubePublishJob,
)
from app.youtube_connections import refresh_channel_credentials
from app.youtube_publish_files import resolve_final_video_path


UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_tags(value: str) -> list[str]:
    try:
        tags = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []

    return [
        str(tag).strip()
        for tag in tags
        if str(tag).strip()
    ]


def format_publish_at(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.isoformat().replace("+00:00", "Z")


def build_video_body(
    publish_job: YouTubePublishJob,
) -> dict[str, object]:
    snippet: dict[str, object] = {
        "title": publish_job.title,
        "description": publish_job.description or "",
        "categoryId": publish_job.category_id or "27",
    }

    tags = load_tags(publish_job.tags)

    if tags:
        snippet["tags"] = tags

    if publish_job.language:
        snippet["defaultLanguage"] = publish_job.language

    status: dict[str, object] = {
        "privacyStatus": publish_job.privacy_status,
        "selfDeclaredMadeForKids": bool(
            publish_job.made_for_kids
        ),
    }

    if publish_job.contains_synthetic_media is not None:
        status["containsSyntheticMedia"] = bool(
            publish_job.contains_synthetic_media
        )

    if publish_job.scheduled_at is not None:
        status["privacyStatus"] = "private"
        status["publishAt"] = format_publish_at(
            publish_job.scheduled_at
        )

    return {
        "snippet": snippet,
        "status": status,
    }


def update_upload_progress(
    publish_job_id: int,
    *,
    progress: int,
    bytes_uploaded: int,
    resumable_uri: str | None,
) -> None:
    with SessionLocal() as db:
        publish_job = db.get(
            YouTubePublishJob,
            publish_job_id,
        )

        if publish_job is None:
            return

        publish_job.status = "uploading"
        publish_job.progress = max(
            0,
            min(99, int(progress)),
        )
        publish_job.bytes_uploaded = max(
            0,
            int(bytes_uploaded),
        )

        if resumable_uri:
            publish_job.encrypted_upload_session_uri = (
                encrypt_value(resumable_uri)
            )

        db.commit()



def upload_youtube_thumbnail(
    publish_job_id: int,
    youtube,
    youtube_video_id: str,
) -> None:
    with SessionLocal() as db:
        publish_job = db.get(
            YouTubePublishJob,
            publish_job_id,
        )

        if publish_job is None:
            return

        if not publish_job.thumbnail_path:
            if publish_job.thumbnail_status == "not_started":
                publish_job.thumbnail_status = "not_requested"
                db.commit()
            return

        thumbnail_path = Path(
            publish_job.thumbnail_path
        ).expanduser().resolve()

        if not thumbnail_path.is_file():
            publish_job.thumbnail_status = "failed"
            publish_job.thumbnail_error = (
                "Generated thumbnail file was not found."
            )
            db.commit()
            return

        if thumbnail_path.stat().st_size >= 2 * 1024 * 1024:
            publish_job.thumbnail_status = "failed"
            publish_job.thumbnail_error = (
                "Thumbnail exceeds the 2 MB safe limit."
            )
            db.commit()
            return

        publish_job.thumbnail_status = "uploading"
        publish_job.thumbnail_error = None
        db.commit()

    mimetype = (
        "image/png"
        if thumbnail_path.suffix.lower() == ".png"
        else "image/jpeg"
    )

    try:
        media = MediaFileUpload(
            str(thumbnail_path),
            mimetype=mimetype,
            resumable=False,
        )

        youtube.thumbnails().set(
            videoId=youtube_video_id,
            media_body=media,
        ).execute()

    except Exception as error:
        with SessionLocal() as db:
            publish_job = db.get(
                YouTubePublishJob,
                publish_job_id,
            )

            if publish_job is not None:
                publish_job.thumbnail_status = "failed"
                publish_job.thumbnail_error = str(error)[:4000]
                db.commit()

        return

    with SessionLocal() as db:
        publish_job = db.get(
            YouTubePublishJob,
            publish_job_id,
        )

        if publish_job is not None:
            publish_job.thumbnail_status = "uploaded"
            publish_job.thumbnail_error = None
            db.commit()


def run_youtube_upload(publish_job_id: int) -> None:
    with SessionLocal() as db:
        publish_job = db.get(
            YouTubePublishJob,
            publish_job_id,
        )

        if publish_job is None:
            raise RuntimeError(
                "YouTube publishing job was not found."
            )

        if publish_job.youtube_video_id:
            return

        video_job = db.get(
            VideoJob,
            publish_job.video_job_id,
        )
        connection = db.get(
            YouTubeConnection,
            publish_job.youtube_connection_id,
        )

        if video_job is None:
            raise RuntimeError(
                "Source video job was not found."
            )

        if connection is None or not connection.is_active:
            raise RuntimeError(
                "YouTube channel is unavailable or disconnected."
            )

        video_path = resolve_final_video_path(video_job)
        credentials = refresh_channel_credentials(
            db,
            connection,
        )

        publish_job.status = "uploading"
        publish_job.progress = 0
        publish_job.bytes_uploaded = 0
        publish_job.total_bytes = video_path.stat().st_size
        publish_job.attempt_count += 1
        publish_job.last_error = None
        publish_job.started_at = utc_now_naive()
        publish_job.completed_at = None
        db.commit()

        body = build_video_body(publish_job)
        notify_subscribers = bool(
            publish_job.notify_subscribers
        )
        total_bytes = publish_job.total_bytes

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=UPLOAD_CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=notify_subscribers,
    )

    response = None

    try:
        while response is None:
            upload_status, response = request.next_chunk(
                num_retries=3,
            )

            resumable_uri = getattr(
                request,
                "resumable_uri",
                None,
            )

            if upload_status is not None:
                progress = int(
                    upload_status.progress() * 100
                )
                bytes_uploaded = int(
                    getattr(
                        upload_status,
                        "resumable_progress",
                        total_bytes * progress / 100,
                    )
                )

                update_upload_progress(
                    publish_job_id,
                    progress=progress,
                    bytes_uploaded=bytes_uploaded,
                    resumable_uri=resumable_uri,
                )

        youtube_video_id = str(
            response.get("id", "")
        ).strip()

        if not youtube_video_id:
            raise RuntimeError(
                "YouTube upload completed without returning a video ID."
            )

        with SessionLocal() as db:
            publish_job = db.get(
                YouTubePublishJob,
                publish_job_id,
            )

            if publish_job is None:
                return

            publish_job.youtube_video_id = youtube_video_id
            publish_job.youtube_url = (
                "https://www.youtube.com/watch?v="
                + youtube_video_id
            )
            publish_job.status = "uploaded"
            publish_job.progress = 100
            publish_job.bytes_uploaded = (
                publish_job.total_bytes
            )
            publish_job.encrypted_upload_session_uri = None
            publish_job.last_error = None
            publish_job.completed_at = utc_now_naive()
            db.commit()

        upload_youtube_thumbnail(
            publish_job_id,
            youtube,
            youtube_video_id,
        )

    except Exception as error:
        with SessionLocal() as db:
            publish_job = db.get(
                YouTubePublishJob,
                publish_job_id,
            )

            if publish_job is not None:
                publish_job.status = (
                    "needs_review"
                    if publish_job.encrypted_upload_session_uri
                    else "failed"
                )
                publish_job.last_error = str(error)[:4000]
                db.commit()

        raise


if __name__ == "__main__":
    run_youtube_upload(int(sys.argv[1]))
