from __future__ import annotations

import importlib.util

from sqlalchemy import func, select

from app.api_settings import has_api_key
from app.database import SessionLocal
from app.models import YouTubeConnection


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError):
        return False


def collect_publishing_status() -> dict[str, object]:
    google_api_available = _module_available("googleapiclient")
    google_auth_available = (
        _module_available("google.auth")
        and _module_available("google.oauth2")
        and _module_available("google_auth_oauthlib")
    )

    youtube_client_id_ready = has_api_key(
        "youtube_client_id"
    )
    youtube_client_secret_ready = has_api_key(
        "youtube_client_secret"
    )

    credentials_ready = (
        youtube_client_id_ready
        and youtube_client_secret_ready
    )

    with SessionLocal() as db:
        connected_channels = db.scalar(
            select(func.count())
            .select_from(YouTubeConnection)
            .where(
                YouTubeConnection.is_active.is_(True),
                YouTubeConnection.encrypted_refresh_token.is_not(
                    None
                ),
            )
        ) or 0

    libraries_ready = (
        google_api_available
        and google_auth_available
    )

    ready = (
        libraries_ready
        and credentials_ready
        and connected_channels > 0
    )

    if ready:
        label = "Ready"
        detail = (
            f"{connected_channels} YouTube channel"
            f"{'s' if connected_channels != 1 else ''} connected."
        )
    elif libraries_ready and credentials_ready:
        label = "Connect channel"
        detail = (
            "OAuth credentials are configured. Connect a YouTube "
            "channel to enable publishing."
        )
    elif credentials_ready:
        label = "Library required"
        detail = (
            "OAuth credentials are configured, but Google API "
            "libraries are unavailable."
        )
    elif libraries_ready:
        label = "OAuth required"
        detail = (
            "Google API libraries are ready. Add the OAuth Client "
            "ID and Client Secret in API Settings."
        )
    else:
        label = "Not configured"
        detail = (
            "Google API libraries and YouTube OAuth credentials "
            "are not configured."
        )

    return {
        "ready": ready,
        "label": label,
        "detail": detail,
        "libraries_ready": libraries_ready,
        "credentials_ready": credentials_ready,
        "google_api_available": google_api_available,
        "google_auth_available": google_auth_available,
        "connected_channels": connected_channels,
    }
