from dataclasses import dataclass
import time
import httpx

from app.api_settings import get_api_key
from app.config import settings

GEMINI_MODEL = "gemini-2.5-flash"
TOGETHER_MODEL = "openai/gpt-oss-120b"


@dataclass
class AIResult:
    text: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0


def call_gemini(prompt, json_mode=False):
    key = get_api_key("gemini_api_key")
    if not key:
        raise RuntimeError("Gemini key is not configured.")

    config = {
        "temperature": 0.7,
        "maxOutputTokens": 12000,
    }
    if json_mode:
        config["responseMimeType"] = "application/json"

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": config,
    }

    last_error = "Gemini request failed."
    for delay in (0, 3, 8):
        time.sleep(delay)
        response = httpx.post(
            url,
            headers={"x-goog-api-key": key},
            json=payload,
            timeout=180,
        )
        if response.status_code == 200:
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(x.get("text", "") for x in parts)
            usage = data.get("usageMetadata", {})
            return AIResult(
                text=text.strip(),
                provider="gemini",
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get(
                    "candidatesTokenCount", 0
                ),
            )

        last_error = response.text[:500]
        if response.status_code not in (429, 500, 502, 503, 504):
            break

    raise RuntimeError(last_error)


def call_together(prompt, json_mode=False):
    key = get_api_key("together_api_key")
    if not key:
        raise RuntimeError("Together key is not configured.")

    payload = {
        "model": TOGETHER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 4500,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    response = httpx.post(
        "https://api.together.xyz/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()
    usage = data.get("usage", {})

    return AIResult(
        text=data["choices"][0]["message"]["content"].strip(),
        provider="together",
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )


def call_ollama(prompt, json_mode=False):
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"

    response = httpx.post(
        f"{settings.ollama_url}/api/generate",
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
    data = response.json()

    return AIResult(
        text=data.get("response", "").strip(),
        provider="ollama",
        input_tokens=data.get("prompt_eval_count", 0),
        output_tokens=data.get("eval_count", 0),
    )


def call_ai(prompt, json_mode=False):
    errors = []

    for provider in (call_gemini, call_ollama, call_together):
        try:
            result = provider(prompt, json_mode=json_mode)
            if not result.text:
                raise RuntimeError("AI returned empty output.")
            return result
        except Exception as error:
            errors.append(f"{provider.__name__}: {error}")

    raise RuntimeError("All AI providers failed: " + " | ".join(errors))
