"""Safe lightweight connection tests; never performs AI generation."""
from datetime import datetime, timezone
import threading
import httpx
from app.api_settings import get_api_key
from app.openverse_client import openverse_headers
from app.publishing_status import collect_publishing_status

TIMEOUT = 15
_results = {}
_lock = threading.Lock()

def _result(provider, status, detail):
    return {"provider": provider, "status": status, "detail": detail,
            "tested_at": datetime.now(timezone.utc).isoformat()}

def _classify(provider, response):
    code = response.status_code
    status = ("Working" if 200 <= code < 300 else "Rate Limited" if code == 429
              else "Authentication Failed" if code in {401, 403} else "Unavailable")
    return _result(provider, status, f"HTTP {code}")

def _get(provider, configured, client, url, **kwargs):
    if not configured:
        return _result(provider, "Configuration Missing", "Not configured")
    try:
        return _classify(provider, client.get(url, timeout=TIMEOUT, **kwargs))
    except httpx.TimeoutException:
        return _result(provider, "Unavailable", "Timeout")
    except Exception:
        return _result(provider, "Unavailable", "Connection error")

def test_provider(provider, client=httpx):
    if provider == "pexels":
        key = get_api_key("pexels_api_key")
        result = _get("Pexels", bool(key), client, "https://api.pexels.com/v1/search",
                      headers={"Authorization": key}, params={"query": "nature", "per_page": 1})
    elif provider == "pixabay":
        key = get_api_key("pixabay_api_key")
        result = _get("Pixabay", bool(key), client, "https://pixabay.com/api/",
                      params={"key": key, "q": "nature", "per_page": 3})
    elif provider == "coverr":
        key = get_api_key("coverr_api_key")
        result = _get("Coverr", bool(key), client, "https://api.coverr.co/videos",
                      headers={"Authorization": f"Bearer {key}"},
                      params={"query": "nature", "page_size": 1, "urls": "false"})
    elif provider == "openverse":
        configured = bool(get_api_key("openverse_client_id") and get_api_key("openverse_client_secret"))
        result = _get("Openverse", configured, client, "https://api.openverse.org/v1/images/",
                      headers=openverse_headers() if configured else {},
                      params={"q": "nature", "page_size": 1})
    elif provider == "gemini":
        key = get_api_key("gemini_api_key")
        result = _get("Google Gemini", bool(key), client,
                      "https://generativelanguage.googleapis.com/v1beta/models",
                      headers={"x-goog-api-key": key}, params={"pageSize": 1})
    elif provider == "together":
        key = get_api_key("together_api_key")
        result = _get("Together AI", bool(key), client, "https://api.together.xyz/v1/models",
                      headers={"Authorization": f"Bearer {key}"})
    elif provider == "youtube":
        state = collect_publishing_status()
        if not state["credentials_ready"]:
            result = _result("YouTube", "Configuration Missing", "OAuth credentials missing")
        elif not state["libraries_ready"]:
            result = _result("YouTube", "Unavailable", "Google API library unavailable")
        elif state["connected_channels"]:
            result = _result("YouTube", "Working", f"Configured connection · {state['connected_channels']} channel(s)")
        else:
            result = _result("YouTube", "Unavailable", "OAuth configured; channel not connected")
    else:
        raise KeyError(provider)
    with _lock:
        _results[provider] = result
    return result

def get_test_results():
    with _lock:
        return dict(_results)
