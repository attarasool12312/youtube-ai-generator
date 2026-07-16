import time
import httpx

from app.api_settings import get_api_key

TOKEN_URL = "https://api.openverse.org/v1/auth_tokens/token/"
_token = ""
_expires_at = 0.0


def get_openverse_token():
    global _token, _expires_at

    if _token and time.time() < _expires_at:
        return _token

    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": get_api_key("openverse_client_id"),
            "client_secret": get_api_key("openverse_client_secret"),
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    _token = data["access_token"]
    _expires_at = time.time() + int(data.get("expires_in", 3600)) - 60
    return _token


def openverse_headers():
    return {"Authorization": f"Bearer {get_openverse_token()}"}
