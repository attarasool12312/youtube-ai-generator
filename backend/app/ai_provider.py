from dataclasses import dataclass
import time
import logging
import os
import httpx

from app.api_settings import get_api_key
from app.config import settings
from app.gemini_router import generate, generate_model

GEMINI_MODEL = "gemini-2.5-flash"
TOGETHER_MODEL = "openai/gpt-oss-120b"
TOGETHER_SCRIPT_MODEL = "deepseek-ai/DeepSeek-V4-Pro-0813"
TOGETHER_SCRIPT_FALLBACK_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"

logger = logging.getLogger(__name__)

# Reuse TCP/TLS connections across Together calls. This changes transport only;
# model, prompts, generation settings, pacing and retry behavior stay unchanged.
_TOGETHER_CLIENT = httpx.Client(
    limits=httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10,
        keepalive_expiry=60.0,
    )
)


@dataclass
class AIResult:
    text: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0


def call_gemini(prompt, json_mode=False):
    data = generate(prompt, json_mode)
    return AIResult(
        text=data["text"],
        provider="gemini:" + data["model"],
        input_tokens=data["input_tokens"],
        output_tokens=data["output_tokens"],
    )


def _call_exact_gemini(prompt, model, json_mode=False):
    data = generate_model(
        prompt,
        model,
        json_mode=json_mode,
    )
    return AIResult(
        text=data["text"],
        provider="gemini:" + data["model"],
        input_tokens=data["input_tokens"],
        output_tokens=data["output_tokens"],
    )


def call_gemini_35_flash_lite(prompt, json_mode=False):
    return _call_exact_gemini(
        prompt,
        "gemini-3.5-flash-lite",
        json_mode=json_mode,
    )


def call_gemini_31_flash_lite(prompt, json_mode=False):
    return _call_exact_gemini(
        prompt,
        "gemini-3.1-flash-lite",
        json_mode=json_mode,
    )



def _call_together_model(
    prompt,
    model,
    json_mode=False,
    temperature=0.35,
    max_tokens=4500,
    timeout=240,
    reasoning_enabled=None,
):
    enabled = os.getenv(
        "ENABLE_TOGETHER_FALLBACK", "true"
    ).lower() in ("1", "true", "yes")
    if not enabled:
        raise RuntimeError("Together fallback disabled.")

    key = get_api_key("together_api_key")
    if not key:
        raise RuntimeError("Together key is not configured.")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    if reasoning_enabled is not None:
        payload["reasoning"] = {"enabled": bool(reasoning_enabled)}

    response = None

    for attempt in range(4):
        response = _TOGETHER_CLIENT.post(
            "https://api.together.xyz/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=timeout,
        )

        if response.status_code != 429:
            break

        retry_after = response.headers.get("retry-after")
        try:
            wait_seconds = float(retry_after)
        except (TypeError, ValueError):
            wait_seconds = (5, 10, 20, 30)[attempt]

        logger.warning(
            "TOGETHER_RATE_LIMIT model=%s attempt=%s wait=%.1fs",
            model,
            attempt + 1,
            wait_seconds,
        )

        if attempt < 3:
            time.sleep(wait_seconds)

    response.raise_for_status()
    data = response.json()
    usage = data.get("usage", {})
    message = (data.get("choices") or [{}])[0].get("message") or {}

    # Never treat reasoning_content as user-facing output.
    text = str(message.get("content") or "").strip()
    if not text:
        raise RuntimeError(
            f"Together model {model} returned no final content."
        )

    return AIResult(
        text=text,
        provider=f"together:{model}",
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )


def call_together(prompt, json_mode=False):
    return _call_together_model(
        prompt,
        TOGETHER_MODEL,
        json_mode=json_mode,
        temperature=0.7,
        max_tokens=4500,
        timeout=180,
    )


def call_deepseek_script_pro(prompt, json_mode=False):
    # DeepSeek V4 Pro default reasoning mode is intentionally retained.
    return _call_together_model(
        prompt,
        TOGETHER_SCRIPT_MODEL,
        json_mode=json_mode,
        temperature=0.35,
        max_tokens=4500,
        timeout=240,
        reasoning_enabled=False,
    )


def call_deepseek_script_flash(prompt, json_mode=False):
    return _call_together_model(
        prompt,
        TOGETHER_SCRIPT_FALLBACK_MODEL,
        json_mode=json_mode,
        temperature=0.35,
        max_tokens=4000,
        timeout=180,
        reasoning_enabled=False,
    )



def call_deepseek_metadata_flash(prompt, json_mode=False):
    return _call_together_model(
        prompt,
        TOGETHER_SCRIPT_FALLBACK_MODEL,
        json_mode=json_mode,
        temperature=0.25,
        max_tokens=5000,
        timeout=180,
        reasoning_enabled=False,
    )



def call_ollama(prompt, json_mode=False):
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
    }
    if json_mode:
        payload["format"] = "json"
        payload["think"] = False

    response = httpx.post(
        f"{settings.ollama_url}/api/generate",
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
    data = response.json()

    return AIResult(
        text=data.get("response", "").strip(),
        provider=f"ollama:{settings.ollama_model}",
        input_tokens=data.get("prompt_eval_count", 0),
        output_tokens=data.get("eval_count", 0),
    )


def call_ollama_length_repair(prompt, maximum_words):
    num_predict = max(
        300,
        min(2400, int(maximum_words * 2.2)),
    )

    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.1,
            "top_p": 0.8,
            "repeat_penalty": 1.1,
            "num_predict": num_predict,
        },
    }

    response = httpx.post(
        f"{settings.ollama_url}/api/generate",
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
    data = response.json()

    return AIResult(
        text=data.get("response", "").strip(),
        provider=(
            f"ollama-length-repair:"
            f"{settings.ollama_model}"
        ),
        input_tokens=data.get("prompt_eval_count", 0),
        output_tokens=data.get("eval_count", 0),
    )


def call_ai(prompt, json_mode=False):
    errors = []

    for provider in (
        call_gemini,
        call_ollama,
        call_together,
    ):
        try:
            result = provider(prompt, json_mode=json_mode)
            if not result.text:
                raise RuntimeError("AI returned empty output.")
            return result
        except Exception as error:
            errors.append(f"{provider.__name__}: {error}")

    raise RuntimeError("All AI providers failed: " + " | ".join(errors))
