import json

from app.ai_provider import (
    call_ollama,
    call_gemini,
    call_together,
)


def decode_json(text):
    text = text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]

    positions = [
        value for value in (text.find("{"), text.find("["))
        if value >= 0
    ]

    if not positions:
        raise ValueError("No JSON found in AI response.")

    value, _ = json.JSONDecoder().raw_decode(
        text[min(positions):]
    )
    return value


def call_scene_json(prompt):
    strict_prompt = prompt + """
Return only JSON: {"scenes":[{"scene_number":1,"stock_keywords":["keyword"],"image_prompt":"visual","video_prompt":"camera shot","media_type":"video"}]}. Include every scene number. No Markdown.
"""

    providers = [
        ("ollama", call_ollama, 2),
        ("gemini", call_gemini, 2),
        ("together", call_together, 1),
    ]

    errors = []

    for name, provider, attempts in providers:
        for attempt in range(attempts):
            try:
                result = provider(
                    strict_prompt,
                    json_mode=True,
                )
                data = decode_json(result.text)
                if isinstance(data, list):
                    data = {"scenes": data}
                if not isinstance(data, dict) or not data.get("scenes"):
                    raise ValueError("Empty scenes JSON.")
                return data
            except Exception as error:
                errors.append(
                    f"{name} #{attempt + 1}: {error}"
                )

    raise RuntimeError("Scene AI failed: " + " | ".join(errors))
