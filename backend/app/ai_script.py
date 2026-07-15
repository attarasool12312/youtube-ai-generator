import json

import httpx

from app.config import settings


def build_prompt(title: str, duration_minutes: int, video_style: str) -> str:
    target_words = duration_minutes * 145
    scene_count = duration_minutes * 8
    return (
        "Create an original YouTube video package.\\n"
        f"Title: {title}\\n"
        f"Duration: {duration_minutes} minutes\\n"
        f"Requested style: {video_style}\\n"
        f"Write between {target_words - 20} and {target_words + 20} narration words.\\n"
        f"Create about {scene_count} scenes.\\n"
        "Each scene narration must contain 17 to 19 words.\\n"
        "Auto-detect the best style when requested style is auto.\\n"
        "Return valid JSON only with: detected_style, script, scenes.\\n"
        "Each scene must contain: scene_number, narration, stock_keywords, "
        "image_prompt, video_prompt, media_type and duration_seconds."
    )




def normalize_scene_durations(scenes, target_seconds):
    if not scenes:
        raise ValueError("No scenes generated.")

    total = sum(max(1, int(x.get("duration_seconds", 1))) for x in scenes)
    remaining = target_seconds

    for index, scene in enumerate(scenes):
        if index == len(scenes) - 1:
            duration = remaining
        else:
            original = max(1, int(scene.get("duration_seconds", 1)))
            duration = max(1, round(target_seconds * original / total))
            duration = min(duration, remaining - (len(scenes) - index - 1))

        scene["duration_seconds"] = duration
        remaining -= duration

    return scenes


def request_package(title, duration_minutes, video_style, feedback=""):
    payload = {
        "model": settings.ollama_model,
        "prompt": build_prompt(title, duration_minutes, video_style) + feedback,
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0.35, "num_predict": 8192, "num_ctx": 32768},
    }
    response = httpx.post(
        f"{settings.ollama_url}/api/generate",
        json=payload,
        timeout=1800,
    )
    response.raise_for_status()
    data = json.loads(response.json()["response"])
    if not all(k in data for k in ("detected_style", "script", "scenes")):
        raise ValueError("Incomplete Qwen response.")
    return data


def generate_video_package(title, duration_minutes, video_style):
    target_words = duration_minutes * 145
    minimum = target_words - 20
    maximum = target_words + 20
    target_seconds = duration_minutes * 60
    feedback = ""
    last_error = ""

    for attempt in range(1, 4):
        data = request_package(title, duration_minutes, video_style, feedback)
        scenes = data.get("scenes") or []
        script = " ".join(str(x.get("narration", "")).strip() for x in scenes)
        word_count = len(script.split())

        if minimum <= word_count <= maximum:
            data["script"] = script
            data["scenes"] = normalize_scene_durations(scenes, target_seconds)
            data["validation"] = {"attempt": attempt, "word_count": word_count}
            return data

        last_error = f"Generated {word_count} words; required {minimum}-{maximum}."
        feedback = "\\n" + last_error + " Rewrite everything and follow the word range strictly."

    raise ValueError(f"Validation failed after 3 attempts: {last_error}")
