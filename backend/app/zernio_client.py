from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.api_settings import get_api_key


ZERNIO_BASE_URL = "https://zernio.com/api/v1"

ZERNIO_TIMEOUT = httpx.Timeout(
    60.0,
    connect=10.0,
)


class ZernioError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details or {}


def _api_key() -> str:
    api_key = get_api_key("zernio_api_key")

    if not api_key:
        raise ZernioError(
            "Zernio API key is not configured."
        )

    return api_key


def _response_error(
    response: httpx.Response,
) -> ZernioError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    message = (
        payload.get("error")
        or payload.get("message")
        or f"Zernio returned HTTP {response.status_code}."
    )

    code = payload.get("code")
    details = payload.get("details")

    if not isinstance(details, dict):
        details = {}

    return ZernioError(
        str(message),
        status_code=response.status_code,
        code=str(code) if code else None,
        details=details,
    )


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    expected_statuses: set[int] | None = None,
    timeout: httpx.Timeout | float | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Accept": "application/json",
    }

    if json_body is not None:
        headers["Content-Type"] = "application/json"

    if extra_headers:
        headers.update(extra_headers)

    try:
        with httpx.Client(
            base_url=ZERNIO_BASE_URL,
            timeout=timeout or ZERNIO_TIMEOUT,
            follow_redirects=False,
        ) as client:
            response = client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise ZernioError(
            f"Zernio request failed: {exc.__class__.__name__}"
        ) from exc

    allowed = expected_statuses or {200}

    if response.status_code not in allowed:
        raise _response_error(response)

    if not response.content:
        return {}

    try:
        payload = response.json()
    except ValueError as exc:
        raise ZernioError(
            "Zernio returned an invalid JSON response.",
            status_code=response.status_code,
        ) from exc

    if not isinstance(payload, dict):
        raise ZernioError(
            "Zernio returned an unexpected response.",
            status_code=response.status_code,
        )

    return payload


def list_profiles(
    *,
    name: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}

    if name:
        params["name"] = name

    payload = _request(
        "GET",
        "/profiles",
        params=params or None,
    )

    profiles = payload.get("profiles", [])

    if not isinstance(profiles, list):
        return []

    return [
        profile
        for profile in profiles
        if isinstance(profile, dict)
    ]


def create_profile(
    name: str,
    *,
    description: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
    }

    if description:
        body["description"] = description

    payload = _request(
        "POST",
        "/profiles",
        json_body=body,
        extra_headers={
            "Idempotency-Key": (
                idempotency_key or str(uuid.uuid4())
            )
        },
        expected_statuses={201},
    )

    profile = payload.get("profile")

    if not isinstance(profile, dict):
        raise ZernioError(
            "Zernio created a profile but returned no profile data."
        )

    return profile


def get_facebook_connect_url(
    profile_id: str,
    *,
    redirect_url: str | None = None,
) -> str:
    params: dict[str, Any] = {
        "profileId": profile_id,
    }

    if redirect_url:
        params["redirect_url"] = redirect_url

    payload = _request(
        "GET",
        "/connect/facebook",
        params=params,
    )

    auth_url = payload.get("authUrl")

    if not isinstance(auth_url, str) or not auth_url:
        raise ZernioError(
            "Zernio did not return a Facebook authorization URL."
        )

    return auth_url


def list_facebook_accounts(
    *,
    profile_id: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "platform": "facebook",
    }

    if profile_id:
        params["profileId"] = profile_id

    payload = _request(
        "GET",
        "/accounts",
        params=params,
    )

    accounts = payload.get("accounts", [])

    if not isinstance(accounts, list):
        return []

    return [
        account
        for account in accounts
        if isinstance(account, dict)
    ]


def move_account_to_profile(
    account_id: str,
    profile_id: str,
) -> dict[str, Any]:
    return _request(
        "PATCH",
        f"/accounts/{account_id}",
        json_body={
            "profileId": profile_id,
        },
    )


def disconnect_account(
    account_id: str,
) -> dict[str, Any]:
    return _request(
        "DELETE",
        f"/accounts/{account_id}",
        expected_statuses={200, 204},
    )


def create_media_upload(
    filename: str,
    content_type: str,
    size: int,
) -> dict[str, Any]:
    return _request(
        "POST",
        "/media/presign",
        json_body={
            "filename": filename,
            "contentType": content_type,
            "size": size,
        },
        expected_statuses={200},
    )


def upload_presigned_file(
    upload_url: str,
    file_path,
    *,
    content_type: str = "video/mp4",
    progress_callback=None,
) -> int:
    from pathlib import Path

    path = Path(file_path)
    total_bytes = path.stat().st_size
    uploaded = 0

    def stream_file():
        nonlocal uploaded

        with path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)

                if not chunk:
                    break

                uploaded += len(chunk)

                if progress_callback:
                    progress_callback(
                        uploaded,
                        total_bytes,
                    )

                yield chunk

    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                3600.0,
                connect=30.0,
            ),
            follow_redirects=True,
        ) as client:
            response = client.put(
                upload_url,
                content=stream_file(),
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(total_bytes),
                },
            )

        response.raise_for_status()

    except httpx.HTTPError as exc:
        raise ZernioError(
            "Zernio media upload failed."
        ) from exc

    return total_bytes


def create_facebook_video_post(
    *,
    account_id: str,
    media_url: str,
    content: str = "",
    title: str = "",
    scheduled_at=None,
    request_id: str | None = None,
) -> dict[str, Any]:
    from datetime import timezone

    body: dict[str, Any] = {
        "content": content,
        "mediaItems": [
            {
                "type": "video",
                "url": media_url,
            }
        ],
        "platforms": [
            {
                "platform": "facebook",
                "accountId": account_id,
            }
        ],
    }

    if title:
        body["title"] = title

    if scheduled_at is None:
        body["publishNow"] = True
    else:
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(
                tzinfo=timezone.utc
            )
        else:
            scheduled_at = scheduled_at.astimezone(
                timezone.utc
            )

        body["scheduledFor"] = (
            scheduled_at.isoformat()
            .replace("+00:00", "Z")
        )
        body["timezone"] = "UTC"

    headers = {}

    if request_id:
        headers["x-request-id"] = request_id

    import time

    payload = None

    for attempt in range(2):
        try:
            payload = _request(
                "POST",
                "/posts",
                json_body=body,
                extra_headers=headers or None,
                expected_statuses={200, 201},
                timeout=httpx.Timeout(
                    900.0,
                    connect=30.0,
                ),
            )
            break
        except ZernioError as error:
            cause = error.__cause__

            if not (
                request_id
                and attempt == 0
                and isinstance(
                    cause,
                    (
                        httpx.ReadTimeout,
                        httpx.ConnectTimeout,
                        httpx.RemoteProtocolError,
                    ),
                )
            ):
                raise

            time.sleep(5)

    if payload is None:
        raise ZernioError(
            "Zernio Facebook post creation returned no response."
        )

    post = (
        payload.get("post")
        or payload.get("existingPost")
    )

    if not isinstance(post, dict):
        raise ZernioError(
            "Zernio returned no Facebook post data."
        )

    return post


def get_post(
    post_id: str,
) -> dict[str, Any]:
    payload = _request(
        "GET",
        f"/posts/{post_id}",
    )

    post = payload.get("post")

    if not isinstance(post, dict):
        raise ZernioError(
            "Zernio returned no post data."
        )

    return post
