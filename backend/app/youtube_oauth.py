from __future__ import annotations

import secrets
from urllib.parse import urlparse

from google_auth_oauthlib.flow import Flow

from app.api_settings import get_api_key
from app.config import settings


YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)

YOUTUBE_OAUTH_CALLBACK_PATH = "/youtube/oauth/callback"


class YouTubeOAuthConfigurationError(RuntimeError):
    pass


def normalize_public_app_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise YouTubeOAuthConfigurationError(
            "Public App URL must be a complete http:// or https:// URL."
        )

    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise YouTubeOAuthConfigurationError(
            "Public App URL must not include a path, query, or fragment."
        )

    local_hosts = {"localhost", "127.0.0.1", "::1"}

    if parsed.scheme == "http" and parsed.hostname not in local_hosts:
        raise YouTubeOAuthConfigurationError(
            "A public domain must use HTTPS."
        )

    return normalized


def get_public_app_url() -> str:
    configured_url = get_api_key("public_app_url").strip()
    return normalize_public_app_url(
        configured_url or settings.app_url
    )


def get_youtube_redirect_uri() -> str:
    return (
        f"{get_public_app_url()}"
        f"{YOUTUBE_OAUTH_CALLBACK_PATH}"
    )


def get_youtube_client_config() -> dict[str, object]:
    client_id = get_api_key("youtube_client_id")
    client_secret = get_api_key("youtube_client_secret")

    if not client_id or not client_secret:
        raise YouTubeOAuthConfigurationError(
            "YouTube OAuth Client ID and Client Secret are required."
        )

    redirect_uri = get_youtube_redirect_uri()

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def build_youtube_oauth_flow(
    state: str | None = None,
    code_verifier: str | None = None,
) -> Flow:
    flow = Flow.from_client_config(
        get_youtube_client_config(),
        scopes=list(YOUTUBE_SCOPES),
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = get_youtube_redirect_uri()
    return flow


def create_youtube_authorization_url(
) -> tuple[str, str, str]:
    code_verifier = secrets.token_urlsafe(64)

    flow = build_youtube_oauth_flow(
        code_verifier=code_verifier,
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    return authorization_url, state, code_verifier
