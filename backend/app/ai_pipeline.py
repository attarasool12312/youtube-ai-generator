import json
import math

import httpx

from app.config import settings


def call_qwen(prompt: str) -> str:
    response = httpx.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.45,
                "num_predict": 4096,
                "num_ctx": 32768,
            },
        },
        timeout=1800,
    )
    response.raise_for_status()
    return response.json()["response"].strip()


def count_words(text: str) -> int:
    return len(text.split())


def trim_words(text: str, maximum: int) -> str:
    words = text.split()
    return " ".join(words[:maximum])


def generate_section(
    title: str,
    video_style: str,
    section_name: str,
    target_words: int,
    previous_context: str = "",
) -> str:
    prompt = (
        f"Video title: {title}\n"
        f"Style: {video_style}\n"
        f"Section: {section_name}\n"
        f"Write at least {target_words + 20} words.\n"
        "Return narration only. No headings or notes.\n"
        f"Previous context: {previous_context[-800:]}"
    )

    text = call_qwen(prompt)

    for _ in range(3):
        if count_words(text) >= target_words:
            return trim_words(text, target_words)

        text = call_qwen(
            f"Expand this narration to at least {target_words + 20} words. "
            "Return only the complete revised narration:\n\n"
            f"{text}"
        )

    raise ValueError(f"Could not generate section: {section_name}")


def split_targets(total_words: int, section_count: int) -> list[int]:
    base = total_words // section_count
    remainder = total_words % section_count

    return [
        base + (1 if index < remainder else 0)
        for index in range(section_count)
    ]


def generate_script(title: str, duration_minutes: int, video_style: str) -> dict:
    target_words = duration_minutes * 145
    body_count = max(2, math.ceil(duration_minutes / 2))

    section_names = [
        "Strong opening hook",
        "Introduction and context",
        *[f"Main section {number}" for number in range(1, body_count + 1)],
        "Conclusion and final takeaway",
    ]

    targets = split_targets(target_words, len(section_names))

    sections = []
    previous_context = ""

    for section_name, section_target in zip(section_names, targets):
        narration = generate_section(
            title,
            video_style,
            section_name,
            section_target,
            previous_context,
        )

        sections.append({
            "name": section_name,
            "narration": narration,
            "word_count": count_words(narration),
        })

        previous_context += " " + narration

    script = " ".join(section["narration"] for section in sections)

    return {
        "script": script,
        "sections": sections,
        "word_count": count_words(script),
        "target_words": target_words,
    }


def call_qwen_json(prompt: str) -> dict:
    response = httpx.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {
                "temperature": 0.4,
                "num_predict": 8192,
                "num_ctx": 32768,
            },
        },
        timeout=1800,
    )
    response.raise_for_status()
    return json.loads(response.json()["response"])


def split_script_into_scenes(script: str, duration_minutes: int) -> list[dict]:
    words = script.split()
    scene_count = max(8, duration_minutes * 8)

    base_size = len(words) // scene_count
    remainder = len(words) % scene_count

    scenes = []
    position = 0

    for index in range(scene_count):
        size = base_size + (1 if index < remainder else 0)
        narration = " ".join(words[position:position + size])
        position += size

        scenes.append({
            "scene_number": index + 1,
            "narration": narration,
        })



    return scenes

def enrich_scene_batch(title, video_style, scenes):
    prompt = (
        f"Video title: {title}\n"
        f"Style: {video_style}\n"
        "Create media metadata for the supplied scenes.\n"
        "Do not rewrite narration.\n"
        "Return JSON with key scenes.\n"
        "Each scene needs scene_number, stock_keywords, "
        "image_prompt, video_prompt and media_type.\n"
        f"Scenes: {json.dumps(scenes)}"
    )

    result = call_qwen_json(prompt)

    metadata = {
        int(item["scene_number"]): item
        for item in result.get("scenes", [])
    }

    enriched = []

    for scene in scenes:
        item = metadata.get(scene["scene_number"])

        if not item:
            raise ValueError(
                f"Missing metadata for scene {scene['scene_number']}."
            )

        enriched.append({
            **scene,
            "stock_keywords": item.get("stock_keywords", []),
            "image_prompt": item.get("image_prompt", ""),
            "video_prompt": item.get("video_prompt", ""),
            "media_type": item.get("media_type", "stock_video"),
        })

    return enriched


def detect_video_style(title, requested_style):
    if requested_style != "auto":
        return requested_style

    result = call_qwen(
        f"Choose the best YouTube style for this title: {title}\n"
        "Choose exactly one: documentary, storytelling, educational, "
        "listicle, news, motivational.\n"
        "Return only the style name."
    ).lower().strip()

    allowed = {
        "documentary",
        "storytelling",
        "educational",
        "listicle",
        "news",
        "motivational",
    }

    return result if result in allowed else "documentary"


def assign_scene_durations(scenes, total_seconds):
    weights = [
        max(1, len(scene["narration"].split()))
        for scene in scenes
    ]

    total_weight = sum(weights)

    durations = [
        max(3, round(total_seconds * weight / total_weight))
        for weight in weights
    ]

    difference = total_seconds - sum(durations)
    index = 0

    while difference != 0:
        position = index % len(durations)

        if difference > 0:
            durations[position] += 1
            difference -= 1
        elif durations[position] > 3:
            durations[position] -= 1
            difference += 1

        index += 1

    for scene, duration in zip(scenes, durations):
        scene["duration_seconds"] = duration

    return scenes


def generate_video_package(title, duration_minutes, video_style):
    detected_style = detect_video_style(title, video_style)

    script_result = generate_script(
        title,
        duration_minutes,
        detected_style,
    )

    scenes = split_script_into_scenes(
        script_result["script"],
        duration_minutes,
    )

    scenes = enrich_scene_batch(
        title,
        detected_style,
        scenes,
    )

    scenes = assign_scene_durations(
        scenes,
        duration_minutes * 60,
    )

    return {
        "title": title,
        "requested_style": video_style,
        "detected_style": detected_style,
        "duration_minutes": duration_minutes,
        "script": script_result["script"],
        "word_count": script_result["word_count"],
        "sections": script_result["sections"],
        "scenes": scenes,
    }
