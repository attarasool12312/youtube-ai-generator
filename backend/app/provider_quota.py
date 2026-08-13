"""Safe, cached provider limit metadata for the API Settings page."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from typing import Any

import httpx

from app.api_settings import get_api_key
from app.openverse_client import openverse_headers


CACHE_TTL_SECONDS = 300
NOT_EXPOSED = "Not exposed by provider"
_cache: dict[str, Any] = {"expires": 0.0, "providers": None}
_cache_lock = threading.Lock()


def _integer_header(headers, name):
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _readable_utc(epoch):
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _status(limit, remaining, http_status):
    if http_status == 429:
        return "rate_limited"
    if http_status is None or http_status >= 400:
        return "unavailable"
    if limit is None or remaining is None or limit <= 0:
        return "not_exposed"
    if remaining <= 0:
        return "exhausted"
    percent = remaining / limit
    if percent < 0.10:
        return "low"
    if percent <= 0.25:
        return "warning"
    return "healthy"


def _base(provider, connected):
    return {
        "provider": provider,
        "connected": bool(connected),
        "http_status": None,
        "limit": None,
        "remaining": None,
        "used": None,
        "window": None,
        "reset_at": None,
        "reset_in_seconds": None,
        "retry_after": None,
        "status": "not_exposed" if connected else "unavailable",
        "quota_message": NOT_EXPOSED if connected else "Not configured",
    }


def _from_headers(provider, connected, response, window, reset_is_epoch):
    item = _base(provider, connected)
    limit = _integer_header(response.headers, "X-RateLimit-Limit")
    remaining = _integer_header(response.headers, "X-RateLimit-Remaining")
    reset = _integer_header(response.headers, "X-RateLimit-Reset")
    retry_after = _integer_header(response.headers, "Retry-After")
    item.update({
        "http_status": response.status_code,
        "limit": limit,
        "remaining": remaining,
        "used": max(0, limit - remaining)
        if limit is not None and remaining is not None else None,
        "window": window if limit is not None else None,
        "reset_at": _readable_utc(reset) if reset_is_epoch else None,
        "reset_in_seconds": reset if reset is not None and not reset_is_epoch else None,
        "retry_after": retry_after,
        "status": _status(limit, remaining, response.status_code),
        "quota_message": None if limit is not None or remaining is not None else NOT_EXPOSED,
    })
    return item


def check_pexels(client=httpx):
    key = get_api_key("pexels_api_key")
    if not key:
        return _base("Pexels", False)
    try:
        response = client.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": key},
            params={"query": "nature", "per_page": 1},
            timeout=15,
        )
        return _from_headers("Pexels", True, response, "month", True)
    except Exception:
        return {**_base("Pexels", True), "status": "unavailable"}


def check_pixabay(client=httpx):
    key = get_api_key("pixabay_api_key")
    if not key:
        return _base("Pixabay", False)
    try:
        response = client.get(
            "https://pixabay.com/api/",
            params={"key": key, "q": "status", "per_page": 3},
            timeout=15,
        )
        return _from_headers("Pixabay", True, response, "60 seconds", False)
    except Exception:
        return {**_base("Pixabay", True), "status": "unavailable"}


def check_coverr(client=httpx):
    key = get_api_key("coverr_api_key")
    if not key:
        return _base("Coverr", False)
    try:
        response = client.get(
            "https://api.coverr.co/videos",
            headers={"Authorization": f"Bearer {key}"},
            params={"query": "status", "page_size": 1, "urls": "false"},
            timeout=15,
        )
        return _from_headers("Coverr", True, response, None, False)
    except Exception:
        return {**_base("Coverr", True), "status": "unavailable"}


def check_openverse(client=httpx):
    configured = bool(
        get_api_key("openverse_client_id")
        and get_api_key("openverse_client_secret")
    )
    if not configured:
        return _base("Openverse", False)
    try:
        response = client.get(
            "https://api.openverse.org/v1/images/",
            headers=openverse_headers(),
            params={"q": "status", "page_size": 1},
            timeout=15,
        )
        item = _from_headers("Openverse", True, response, None, False)
        item["connection_detail"] = "Authenticated"
        return item
    except Exception:
        return {**_base("Openverse", True), "status": "unavailable"}


def _non_queryable(provider, configured):
    return _base(provider, configured)


def _collect_uncached():
    providers = [
        check_pexels(),
        check_pixabay(),
        check_coverr(),
        check_openverse(),
        _non_queryable("Google Gemini", bool(get_api_key("gemini_api_key"))),
        _non_queryable("Together AI", bool(get_api_key("together_api_key"))),
        _non_queryable(
            "YouTube",
            bool(get_api_key("youtube_client_id") and get_api_key("youtube_client_secret")),
        ),
    ]
    checked_at = datetime.now(timezone.utc).isoformat()
    for item in providers:
        item["checked_at"] = checked_at
    return providers


def get_provider_quotas(force_refresh=False):
    now = time.monotonic()
    with _cache_lock:
        if not force_refresh and _cache["providers"] is not None and now < _cache["expires"]:
            return [dict(item, cache_state="Cached") for item in _cache["providers"]]
        providers = _collect_uncached()
        _cache.update({"providers": providers, "expires": now + CACHE_TTL_SECONDS})
        return [dict(item, cache_state="Live") for item in providers]


def clear_quota_cache():
    with _cache_lock:
        _cache.update({"providers": None, "expires": 0.0})
