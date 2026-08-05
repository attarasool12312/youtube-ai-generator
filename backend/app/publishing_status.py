from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def _configured(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _configured_file(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value and Path(value).expanduser().is_file())


def collect_publishing_status() -> dict[str, object]:
    google_api_available = (
        importlib.util.find_spec("googleapiclient") is not None
    )

    google_auth_available = (
        importlib.util.find_spec("google.auth") is not None
        and importlib.util.find_spec("google.oauth2") is not None
    )

    oauth_file_ready = any([
        _configured_file("YOUTUBE_CLIENT_SECRETS_FILE"),
        _configured_file("GOOGLE_CLIENT_SECRETS_FILE"),
    ])

    oauth_environment_ready = all([
        _configured("YOUTUBE_CLIENT_ID"),
        _configured("YOUTUBE_CLIENT_SECRET"),
        _configured("YOUTUBE_REFRESH_TOKEN"),
    ])

    credentials_ready = (
        oauth_file_ready or oauth_environment_ready
    )

    libraries_ready = (
        google_api_available and google_auth_available
    )

    ready = credentials_ready and libraries_ready

    if ready:
        label = "Ready"
        detail = "YouTube publishing integration is available."
    elif credentials_ready and not libraries_ready:
        label = "Library required"
        detail = (
            "OAuth credentials were detected, but the Google API "
            "client libraries are not installed."
        )
    elif libraries_ready and not credentials_ready:
        label = "OAuth required"
        detail = (
            "Google API libraries are installed, but YouTube OAuth "
            "credentials are not configured."
        )
    else:
        label = "Not configured"
        detail = (
            "YouTube OAuth credentials and Google API client "
            "libraries have not been configured."
        )

    return {
        "ready": ready,
        "label": label,
        "detail": detail,
        "libraries_ready": libraries_ready,
        "credentials_ready": credentials_ready,
        "google_api_available": google_api_available,
        "google_auth_available": google_auth_available,
        "oauth_file_ready": oauth_file_ready,
        "oauth_environment_ready": oauth_environment_ready,
    }
