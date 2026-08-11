import json
import math
import re

import httpx

from app.config import settings
from app.ai_provider import (
    call_ai, call_gemini, call_ollama, call_together,
)
from app.scene_ai import call_scene_json
from app.script_validator import validate_script
from app.script_workflow import create_validated_script
from app.video_profiles import (
    MANUAL_PROFILE_KEYS,
    normalize_profile_key,
    profile_detection_prompt,
    media_profile_instructions,
)


def call_qwen(prompt: str) -> str:
    result = call_ai(prompt, json_mode=False)
    return result.text


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


def _decode_ai_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]

    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object returned.")

    value, _ = json.JSONDecoder().raw_decode(text[start:])
    return value


def call_qwen_json(prompt: str) -> dict:
    prompt += (
        "\nReturn only complete valid JSON. "
        "No Markdown. Keep every field concise."
    )
    errors = []

    attempts = [
        (call_gemini, 3),
        (call_ollama, 2),
        (call_together, 1),
    ]

    for provider, total in attempts:
        for attempt in range(total):
            try:
                result = provider(prompt, json_mode=True)
                return _decode_ai_json(result.text)
            except Exception as error:
                errors.append(
                    f"{provider.__name__} #{attempt + 1}: {error}"
                )

    raise ValueError("All JSON attempts failed: " + " | ".join(errors))

def split_script_into_scenes(script, duration_minutes):
    words = script.strip().split()

    if not words:
        raise ValueError("Cannot create scenes from an empty script.")

    desired_count = max(1, duration_minutes * 8)
    minimum_words_per_scene = 12

    safe_count = max(
        1,
        len(words) // minimum_words_per_scene,
    )

    count = min(desired_count, safe_count)
    scenes = []
    start = 0

    for index in range(count):
        remaining_scenes = count - index

        if remaining_scenes == 1:
            end = len(words)
        else:
            remaining_words = len(words) - start
            ideal = start + round(
                remaining_words / remaining_scenes
            )

            minimum_end = start + minimum_words_per_scene
            maximum_end = (
                len(words)
                - minimum_words_per_scene
                * (remaining_scenes - 1)
            )

            target = min(
                max(ideal, minimum_end),
                maximum_end,
            )

            search_start = max(
                minimum_end,
                target - 6,
            )
            search_end = min(
                maximum_end,
                target + 6,
            )

            choices = range(
                search_start,
                search_end + 1,
            )

            endings = [
                position
                for position in choices
                if words[position - 1].endswith(
                    (".", "!", "?", ";", ":")
                )
            ]

            end = (
                min(
                    endings,
                    key=lambda position: abs(
                        position - target
                    ),
                )
                if endings
                else target
            )

        narration = " ".join(words[start:end]).strip()

        if not narration:
            raise ValueError(
                f"Scene {index + 1} has empty narration."
            )

        scenes.append({
            "scene_number": index + 1,
            "narration": narration,
        })

        start = end

    return scenes


def _enrich_scene_batch_once(title, video_style, scenes):
    prompt = (
        f"Video title: {title}\n"
        f"Style: {video_style}\n"
        "Profile-specific media instructions:\n"
        f"{media_profile_instructions(video_style)}\n"
        "Create media metadata for the supplied scenes.\n"
        "Do not rewrite narration.\n"
        "Return JSON with key scenes.\n"
        "Each scene needs scene_number, search_query, stock_keywords, "
        "image_prompt, video_prompt and media_type.\n"
        "search_query must be one concise search phrase describing the "
        "most visually important real subject in that scene.\n"
        "When a scene involves a named person, place, monument, event, "
        "species, company, technology or spacecraft, keep that exact "
        "proper name in search_query.\n"
        "Do not replace specific subjects with generic concepts such as "
        "man, woman, building, office, crowd, technology or history.\n"
        "stock_keywords should support the same exact subject and context.\n"
        f"Scenes: {json.dumps(scenes)}"
    )

    result = call_scene_json(prompt)

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
            "search_query": item.get("search_query", ""),
            "stock_keywords": item.get("stock_keywords", []),
            "image_prompt": item.get("image_prompt", ""),
            "video_prompt": item.get("video_prompt", ""),
            "media_type": item.get("media_type", "stock_video"),
        })

    return enriched


def detect_video_style(title, requested_style):
    requested_style = normalize_profile_key(requested_style)

    if requested_style != "auto":
        return requested_style

    result = call_qwen(
        profile_detection_prompt(title)
    ).lower().strip()

    result = result.replace("`", "").strip()

    if "\n" in result:
        result = result.splitlines()[0].strip()

    if ":" in result:
        candidate = result.split(":", 1)[0].strip()
        if candidate in MANUAL_PROFILE_KEYS:
            result = candidate

    return (
        result
        if result in MANUAL_PROFILE_KEYS
        else "general_explainer"
    )



def enrich_scene_batch(title, video_style, scenes):
    enriched = []

    for start in range(0, len(scenes), 6):
        batch = scenes[start:start + 6]
        enriched.extend(
            _enrich_scene_batch_once(
                title, video_style, batch
            )
        )

    return enriched


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


def generate_video_package(title, duration_minutes, video_style, progress_callback=None):
    detected_style = detect_video_style(title, video_style)

    script_result = create_validated_script(
        title,
        duration_minutes,
        detected_style,
        progress_callback=progress_callback,
    )
    validation = script_result["validation"]
    print("SCRIPT_INITIAL_PROVIDER:", script_result["initial_provider"], flush=True)
    print("SCRIPT_REPAIR_PROVIDERS:", script_result["repair_providers"] or ["none"], flush=True)

    if progress_callback:
        progress_callback("scene_planning", 30)

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

    if progress_callback:
        progress_callback("scene_planning", 35)

    return {
        "title": title,
        "requested_style": video_style,
        "detected_style": detected_style,
        "duration_minutes": duration_minutes,
        "script": script_result["script"],
        "word_count": script_result["actual_words"],
        "script_initial_provider": script_result["initial_provider"],
        "script_repair_providers": script_result["repair_providers"],
        "script_validation": validation,
        "sections": [],
        "scenes": scenes,
    }


def repair_script(title, minutes, style, original, report):
    target = minutes * 145
    prompt = (
        f"Rewrite this YouTube narration about: {title}\n"
        f"Style: {style}. Target exactly about {target} words.\n"
        "Remove all repeated sentences, paragraphs and ideas. "
        "Use a clear intro, progressive body and strong conclusion.\n"
        "Return narration only, without headings or notes.\n"
        f"Validation issues: {report['issues']}\n"
        f"Original script:\n{original}"
    )
    result = call_ai(prompt)
    text = result.text.strip()

    return {
        "script": text,
        "sections": [{
            "name": "Repaired Script",
            "narration": text,
            "word_count": count_words(text),
        }],
        "word_count": count_words(text),
        "target_words": target,
        "ai_provider": result.provider,
    }
