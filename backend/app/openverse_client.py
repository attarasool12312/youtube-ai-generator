import time
import httpx

from app.api_settings import get_api_key

TOKEN_URL = "https://api.openverse.org/v1/auth_tokens/token/"
_token = ""
_expires_at = 0.0
_unavailable_until = 0.0


def get_openverse_token():
    global _token, _expires_at, _unavailable_until

    now = time.time()

    if _token and now < _expires_at:
        return _token

    if now < _unavailable_until:
        raise RuntimeError("Openverse auth temporarily unavailable")

    client_id = get_api_key("openverse_client_id")
    client_secret = get_api_key("openverse_client_secret")

    if not client_id or not client_secret:
        _unavailable_until = now + 60
        raise RuntimeError("Openverse credentials are not configured")

    try:
        response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=httpx.Timeout(2.5, connect=1.5),
        )
        response.raise_for_status()
        data = response.json()

        _token = data["access_token"]
        _expires_at = time.time() + int(data.get("expires_in", 3600)) - 60
        _unavailable_until = 0.0
        return _token
    except Exception:
        _unavailable_until = time.time() + 60
        raise


def openverse_headers():
    return {"Authorization": f"Bearer {get_openverse_token()}"}
