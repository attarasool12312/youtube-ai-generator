from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
from datetime import datetime, timezone

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api_settings import (
    decrypt_value,
    encrypt_value,
    get_api_key,
)
from app.models import YouTubeConnection
from app.youtube_oauth import (
    YOUTUBE_SCOPES,
    YouTubeOAuthConfigurationError,
)



GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

logger = logging.getLogger(__name__)

CHANNEL_THUMBNAIL_DIRECTORY = (
    Path(__file__).resolve().parent
    / "static"
    / "youtube-channels"
)


def cache_channel_thumbnail(
    channel_id: str,
    remote_url: str | None,
) -> str | None:
    if not remote_url:
        return None

    safe_channel_id = "".join(
        character
        for character in channel_id
        if character.isalnum() or character in {"-", "_"}
    )

    if not safe_channel_id:
        return None

    try:
        response = httpx.get(
            remote_url,
            follow_redirects=True,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()

        content_type = (
            response.headers
            .get("content-type", "")
            .split(";", 1)[0]
            .lower()
        )

        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(content_type)

        if extension is None:
            raise RuntimeError(
                f"Unsupported thumbnail type: {content_type}"
            )

        CHANNEL_THUMBNAIL_DIRECTORY.mkdir(
            parents=True,
            exist_ok=True,
        )

        target = CHANNEL_THUMBNAIL_DIRECTORY / (
            safe_channel_id + extension
        )
        temporary = target.with_name(target.name + ".tmp")

        temporary.write_bytes(response.content)
        temporary.replace(target)

        return (
            "/static/youtube-channels/"
            + target.name
        )

    except Exception:
        logger.warning(
            "Could not cache YouTube channel thumbnail",
            exc_info=True,
        )
        return None


def ensure_channel_thumbnail_cached(
    db: Session,
    connection: YouTubeConnection,
) -> str | None:
    thumbnail_url = connection.channel_thumbnail_url

    if thumbnail_url and thumbnail_url.startswith("/static/"):
        relative_path = thumbnail_url[len("/static/"):]
        target = (
            Path(__file__).resolve().parent
            / "static"
            / relative_path
        )

        if target.is_file():
            return thumbnail_url

    elif thumbnail_url:
        return thumbnail_url

    if (
        not connection.is_active
        or not connection.encrypted_refresh_token
    ):
        return None

    try:
        credentials = refresh_channel_credentials(
            db,
            connection,
        )
        channel_info = fetch_authorized_channel(
            credentials,
        )

        cached_thumbnail = cache_channel_thumbnail(
            connection.channel_id,
            channel_info["channel_thumbnail_url"],
        )

        if not cached_thumbnail:
            return None

        connection.channel_thumbnail_url = cached_thumbnail
        db.commit()
        db.refresh(connection)

        return cached_thumbnail

    except Exception:
        logger.warning(
            "Could not restore missing YouTube channel thumbnail",
            exc_info=True,
        )
        return None


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_expiry(
    expiry: datetime | None,
) -> datetime | None:
    if expiry is None:
        return None

    if expiry.tzinfo is None:
        return expiry

    return expiry.astimezone(timezone.utc).replace(tzinfo=None)


def _serialize_scopes(scopes) -> str:
    return json.dumps(sorted(set(scopes or YOUTUBE_SCOPES)))


def _deserialize_scopes(value: str) -> list[str]:
    try:
        scopes = json.loads(value or "[]")
    except json.JSONDecodeError:
        scopes = []

    return list(scopes or YOUTUBE_SCOPES)


def fetch_authorized_channel(
    credentials: Credentials,
) -> dict[str, str | None]:
    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    response = youtube.channels().list(
        part="snippet",
        mine=True,
    ).execute()

    items = response.get("items", [])

    if not items:
        raise RuntimeError(
            "No YouTube channel was returned for this Google account."
        )

    channel = items[0]
    snippet = channel.get("snippet", {})
    thumbnails = snippet.get("thumbnails", {})

    thumbnail_url = None

    for size in ("high", "medium", "default"):
        candidate = thumbnails.get(size, {}).get("url")
        if candidate:
            thumbnail_url = candidate
            break

    return {
        "channel_id": channel["id"],
        "channel_title": snippet.get("title") or "YouTube Channel",
        "channel_thumbnail_url": thumbnail_url,
    }


def save_connected_channel(
    db: Session,
    credentials: Credentials,
) -> YouTubeConnection:
    channel_info = fetch_authorized_channel(credentials)

    connection = db.scalar(
        select(YouTubeConnection).where(
            YouTubeConnection.channel_id
            == channel_info["channel_id"]
        )
    )

    encrypted_refresh_token = None

    if credentials.refresh_token:
        encrypted_refresh_token = encrypt_value(
            credentials.refresh_token
        )
    elif connection:
        encrypted_refresh_token = (
            connection.encrypted_refresh_token
        )

    if not encrypted_refresh_token:
        raise RuntimeError(
            "Google did not return an offline refresh token. "
            "Reconnect the channel and approve access again."
        )

    encrypted_access_token = (
        encrypt_value(credentials.token)
        if credentials.token
        else None
    )

    if connection is None:
        connection = YouTubeConnection(
            channel_id=channel_info["channel_id"],
            channel_title=channel_info["channel_title"],
        )
        db.add(connection)

    connection.channel_title = channel_info["channel_title"]

    cached_thumbnail = cache_channel_thumbnail(
        channel_info["channel_id"],
        channel_info["channel_thumbnail_url"],
    )

    if cached_thumbnail:
        connection.channel_thumbnail_url = cached_thumbnail
    elif not (
        connection.channel_thumbnail_url
        and connection.channel_thumbnail_url.startswith("/static/")
    ):
        connection.channel_thumbnail_url = None
    connection.encrypted_access_token = encrypted_access_token
    connection.encrypted_refresh_token = encrypted_refresh_token
    connection.token_expiry = _normalize_expiry(
        credentials.expiry
    )
    connection.scopes = _serialize_scopes(
        credentials.scopes
    )
    connection.is_active = True
    connection.last_error = None
    connection.last_verified_at = _utc_now_naive()

    db.commit()
    db.refresh(connection)

    return connection


def build_channel_credentials(
    connection: YouTubeConnection,
) -> Credentials:
    client_id = get_api_key("youtube_client_id")
    client_secret = get_api_key("youtube_client_secret")

    if not client_id or not client_secret:
        raise YouTubeOAuthConfigurationError(
            "YouTube OAuth credentials are not configured."
        )

    if not connection.encrypted_refresh_token:
        raise RuntimeError(
            "This YouTube channel has no saved refresh token."
        )

    access_token = (
        decrypt_value(connection.encrypted_access_token)
        if connection.encrypted_access_token
        else None
    )

    refresh_token = decrypt_value(
        connection.encrypted_refresh_token
    )

    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=_deserialize_scopes(connection.scopes),
        expiry=connection.token_expiry,
    )


def refresh_channel_credentials(
    db: Session,
    connection: YouTubeConnection,
) -> Credentials:
    credentials = build_channel_credentials(connection)

    if credentials.valid:
        return credentials

    credentials.refresh(GoogleAuthRequest())

    connection.encrypted_access_token = (
        encrypt_value(credentials.token)
        if credentials.token
        else None
    )
    connection.token_expiry = _normalize_expiry(
        credentials.expiry
    )
    connection.last_error = None
    connection.last_verified_at = _utc_now_naive()

    db.commit()
    db.refresh(connection)

    return credentials

