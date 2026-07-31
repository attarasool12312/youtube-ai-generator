import json, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx

from app.api_settings import get_api_key

STATE = Path("backend/data/gemini_router_state.json")
CACHE = {"until": 0, "models": []}

FREE_MODEL_POOL = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
)

def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}

def save_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2))

def daily_reset():
    zone = ZoneInfo("America/Los_Angeles")
    now = datetime.now(zone)
    day = (now + timedelta(days=1)).date()
    reset = datetime.combine(
        day, datetime.min.time(), tzinfo=zone
    )
    return reset.timestamp() + 60

def list_models():
    now = time.time()
    if CACHE["models"] and CACHE["until"] > now:
        return CACHE["models"]

    key = get_api_key("gemini_api_key")
    if not key:
        raise RuntimeError("Gemini key missing.")

    response = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": key},
        params={"pageSize": 1000},
        timeout=60,
    )
    response.raise_for_status()

    available = {
        item.get("name", "").removeprefix("models/")
        for item in response.json().get("models", [])
        if "generateContent"
        in item.get("supportedGenerationMethods", [])
    }

    models = [
        model
        for model in FREE_MODEL_POOL
        if model in available
    ]

    if not models:
        raise RuntimeError(
            "No approved Gemini free-tier models are available."
        )

    CACHE["models"] = models
    CACHE["until"] = now + 3600
    return models

def cooldown(status, message):
    low = message.lower()

    daily = any(word in low for word in (
        "perday", "per_day", "per day",
        "requests per day", "rpd", "daily",
    ))

    if status == 429 and daily:
        return daily_reset()
    if status == 429:
        return time.time() + 75
    if status in (400, 404):
        return time.time() + 86400
    return time.time() + 180

def generate(prompt, json_mode=False):
    key = get_api_key("gemini_api_key")
    state = load_state()
    errors = []

    config = {
        "temperature": 0.7,
        "maxOutputTokens": 8192,
    }
    if json_mode:
        config["responseMimeType"] = "application/json"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": config,
    }
    for model in list_models():
        record = state.get(model, {})
        if record.get("disabled_until", 0) > time.time():
            continue

        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:generateContent"
        )

        try:
            response = httpx.post(
                url, headers={"x-goog-api-key": key},
                json=payload, timeout=180,
            )
        except Exception as error:
            errors.append(f"{model}: {error}")
            continue
        if response.status_code == 200:
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(x.get("text", "") for x in parts)
            usage = data.get("usageMetadata", {})

            state[model] = {
                "disabled_until": 0,
                "last_success": time.time(),
            }
            save_state(state)

            return {
                "text": text.strip(),
                "model": model,
                "input_tokens": usage.get("promptTokenCount", 0),
                "output_tokens": usage.get("candidatesTokenCount", 0),
            }
        message = response.text[:800]
        state[model] = {
            "disabled_until": cooldown(
                response.status_code, message
            ),
            "last_error": message[:300],
        }
        save_state(state)
        errors.append(f"{model}: HTTP {response.status_code}")

    raise RuntimeError(
        "Gemini pool unavailable: " + " | ".join(errors)
    )
