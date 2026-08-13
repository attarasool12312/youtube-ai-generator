from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai_provider import call_ai
from app.youtube_categories import YOUTUBE_CATEGORY_OPTIONS
from app.youtube_publish_validation import (
    calculate_tags_length,
    validate_youtube_metadata,
)


CATEGORY_IDS = {
    category_id
    for category_id, _label in YOUTUBE_CATEGORY_OPTIONS
}

CATEGORY_LABELS = "\n".join(
    f"{category_id}: {label}"
    for category_id, label in YOUTUBE_CATEGORY_OPTIONS
)

PROVIDER_LABELS = {
    "pexels": "Pexels",
    "pixabay": "Pixabay",
    "unsplash": "Unsplash",
    "wikimedia": "Wikimedia Commons",
    "openverse": "Openverse",
}

NUMBER_WORD_VALUES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
}

NUMBER_SCALE_VALUES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}


@dataclass(frozen=True)
class GeneratedYouTubeMetadata:
    title: str
    description: str
    tags: list[str]
    category_id: str
    language: str
    provider: str
    stock_credits: str


def _clean_inline(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    return text.replace("<", "").replace(">", "")


def _truncate_title(value: str, maximum: int = 100) -> str:
    value = _clean_inline(value)

    if len(value) <= maximum:
        return value

    shortened = value[:maximum].rsplit(" ", 1)[0].rstrip(" -:|")
    return shortened or value[:maximum]


def _extract_json_object(value: str) -> dict[str, Any]:
    text = value.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            raise ValueError("AI metadata response contained no JSON object.")

        data = json.loads(text[start:end + 1])

    if not isinstance(data, dict):
        raise ValueError("AI metadata response must be a JSON object.")

    return data


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = re.split(r"[,\n]", value)
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []

    tags: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        tag = _clean_inline(candidate).lstrip("#")

        if not tag or len(tag) > 70:
            continue

        key = tag.casefold()

        if key in seen:
            continue

        proposed = [*tags, tag]

        if calculate_tags_length(proposed) > 480:
            break

        seen.add(key)
        tags.append(tag)

        if len(tags) >= 18:
            break

    return tags


def _script_excerpt(script: str) -> str:
    script = script.strip()

    if len(script) <= 6500:
        return script

    return (
        script[:4200].rstrip()
        + "\n\n[Middle section omitted]\n\n"
        + script[-2000:].lstrip()
    )


def _collect_keywords(scenes: list[dict[str, Any]]) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()

    for scene in scenes:
        values = scene.get("stock_keywords") or []

        if isinstance(values, str):
            values = re.split(r"[,\n]", values)

        for value in values:
            keyword = _clean_inline(value)

            if not keyword:
                continue

            key = keyword.casefold()

            if key in seen:
                continue

            seen.add(key)
            keywords.append(keyword)

            if len(keywords) >= 60:
                return keywords

    return keywords


def build_stock_credits(
    scenes: list[dict[str, Any]],
) -> str:
    records: dict[str, dict[str, Any]] = {}

    for scene in scenes:
        source = _clean_inline(scene.get("source"))

        if not source.startswith(("https://", "http://")):
            continue

        provider = _clean_inline(
            scene.get("media_provider")
            or scene.get("provider")
        ).lower()

        if provider in {
            "flux",
            "ai",
            "generated",
            "local",
        }:
            continue

        author = _clean_inline(scene.get("author"))
        license_name = _clean_inline(scene.get("license"))
        license_url = _clean_inline(scene.get("license_url"))
        scene_number = scene.get("scene_number")

        record = records.setdefault(
            source,
            {
                "provider": provider,
                "author": author,
                "license": license_name,
                "license_url": license_url,
                "scenes": [],
            },
        )

        if scene_number is not None:
            record["scenes"].append(str(scene_number))

    lines: list[str] = []

    for source, record in records.items():
        provider = PROVIDER_LABELS.get(
            record["provider"],
            record["provider"].title() or "Stock media",
        )
        author = record["author"] or "Creator not provided"
        scene_numbers = ", ".join(record["scenes"])

        prefix = (
            f"Scene {scene_numbers}"
            if len(record["scenes"]) == 1
            else f"Scenes {scene_numbers}"
        )

        license_text = ""
        if record["license"]:
            license_text = f" — License: {record['license']}"
            if record["license_url"].startswith(("https://", "http://")):
                license_text += f" ({record['license_url']})"
        lines.append(
            f"{prefix}: {provider} — {author}{license_text} — {source}"
        )

    return "\n".join(lines)



def build_compact_stock_credits(
    scenes: list[dict[str, Any]],
) -> str:
    groups: dict[str, dict[str, Any]] = {}

    for scene in scenes:
        source = _clean_inline(scene.get("source"))

        if not source.startswith(("https://", "http://")):
            continue

        provider_key = _clean_inline(
            scene.get("media_provider")
            or scene.get("provider")
        ).lower()

        if provider_key in {
            "flux",
            "ai",
            "generated",
            "local",
        }:
            continue

        provider = PROVIDER_LABELS.get(
            provider_key,
            provider_key.title() or "Stock media",
        )
        author = _clean_inline(scene.get("author"))

        group = groups.setdefault(
            provider,
            {
                "count": 0,
                "authors": [],
                "seen": set(),
            },
        )
        group["count"] += 1

        if author and author.casefold() not in group["seen"]:
            group["seen"].add(author.casefold())
            group["authors"].append(author)

    lines: list[str] = []

    for provider, group in groups.items():
        visible_authors = group["authors"][:6]
        hidden_count = max(
            0,
            len(group["authors"]) - len(visible_authors),
        )
        authors = ", ".join(visible_authors)

        if hidden_count:
            authors += f", and {hidden_count} more"

        asset_word = (
            "asset"
            if group["count"] == 1
            else "assets"
        )

        lines.append(
            f"{provider} ({group['count']} {asset_word}): "
            f"{authors or 'Creator details stored with project'}"
        )

    return "\n".join(lines)


def merge_description_and_credits(
    description: str,
    stock_credits: str,
    maximum_bytes: int = 5000,
) -> str:
    description = description.strip().replace("<", "").replace(">", "")

    if not stock_credits:
        return description

    heading = "\n\nMedia credits:\n"
    combined = description + heading + stock_credits

    if len(combined.encode("utf-8")) <= maximum_bytes:
        return combined

    retained: list[str] = []
    notice = (
        "\nAdditional media source records are stored "
        "with the video project."
    )

    for line in stock_credits.splitlines():
        candidate = (
            description
            + heading
            + "\n".join([*retained, line])
            + notice
        )

        if len(candidate.encode("utf-8")) > maximum_bytes:
            break

        retained.append(line)

    if not retained:
        return description

    return (
        description
        + heading
        + "\n".join(retained)
        + notice
    )



def _number_words_to_value(words: list[str]) -> int:
    total = 0
    current = 0

    for word in words:
        if word == "and":
            continue

        if word in NUMBER_WORD_VALUES:
            current += NUMBER_WORD_VALUES[word]
            continue

        scale = NUMBER_SCALE_VALUES[word]

        if scale == 100:
            current = max(current, 1) * scale
        else:
            total += max(current, 1) * scale
            current = 0

    return total + current


def _extract_numeric_claims(value: str) -> set[str]:
    claims: set[str] = set()
    number_words: list[str] = []

    def flush_number_words() -> None:
        nonlocal number_words

        if number_words:
            claims.add(str(_number_words_to_value(number_words)))
            number_words = []

    tokens = re.findall(
        r"\d[\d,]*(?:\.\d+)?%?"
        r"|[a-z]+(?:-[a-z]+)*",
        value.casefold(),
    )

    valid_words = {
        *NUMBER_WORD_VALUES,
        *NUMBER_SCALE_VALUES,
        "and",
    }

    for token in tokens:
        if token[0].isdigit():
            flush_number_words()
            normalized = token.rstrip("%").replace(",", "")
            claims.add(normalized)
            continue

        for part in token.split("-"):
            if part in valid_words:
                number_words.append(part)
            else:
                flush_number_words()

    flush_number_words()
    return claims


def _validate_numeric_claims(
    source_text: str,
    generated_text: str,
) -> None:
    source_claims = _extract_numeric_claims(source_text)
    generated_claims = _extract_numeric_claims(generated_text)

    unsupported = sorted(generated_claims - source_claims)

    if unsupported:
        raise ValueError(
            "AI metadata introduced unsupported or corrupted "
            f"numeric claims: {', '.join(unsupported)}"
        )


def _fallback_category(title: str, script: str) -> str:
    text = f"{title} {script[:2500]}".casefold()

    rules = (
        ("20", ("game", "gaming", "console", "esports")),
        ("17", ("sport", "football", "basketball", "athlete")),
        ("19", ("travel", "tourism", "destination", "journey")),
        ("15", ("animal", "wildlife", "pet", "species")),
        ("25", ("election", "government", "politics", "breaking news")),
        ("26", ("how to", "tutorial", "step by step", "diy")),
        (
            "28",
            (
                "technology",
                "science",
                "internet",
                "computer",
                "space",
                "engineering",
                "artificial intelligence",
            ),
        ),
        (
            "27",
            (
                "history",
                "explained",
                "education",
                "learn",
                "discovery",
            ),
        ),
    )

    for category_id, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return category_id

    return "22"


def _fallback_metadata(
    package: dict[str, Any],
    stock_credits: str,
) -> GeneratedYouTubeMetadata:
    original_title = _truncate_title(
        package.get("title") or "Untitled Video"
    )
    script = str(package.get("script") or "").strip()
    scenes = package.get("scenes") or []
    compact_stock_credits = build_compact_stock_credits(
        scenes
    )

    sentences = re.split(
        r"(?<=[.!?])\s+",
        " ".join(script.split()),
    )
    summary = " ".join(
        sentence
        for sentence in sentences[:3]
        if sentence
    ).strip()

    if not summary:
        summary = (
            f"Explore {original_title} with a clear, "
            "fact-focused explanation."
        )

    description = (
        f"{original_title}\n\n"
        f"{summary}\n\n"
        "This video provides an accessible explanation of the "
        "topic, its context, and why it matters."
    )

    keyword_candidates = [
        original_title,
        *_collect_keywords(scenes),
    ]
    tags = _normalize_tags(keyword_candidates)

    if not tags:
        tags = [original_title]

    description = merge_description_and_credits(
        description,
        compact_stock_credits,
    )

    category_id = _fallback_category(
        original_title,
        script,
    )

    validated = validate_youtube_metadata(
        title=original_title,
        description=description,
        tags=",".join(tags),
        category_id=category_id,
        language="en",
        privacy_status="private",
    )

    return GeneratedYouTubeMetadata(
        title=validated.title,
        description=validated.description,
        tags=validated.tags,
        category_id=validated.category_id,
        language=validated.language,
        provider="deterministic-fallback",
        stock_credits=stock_credits,
    )


def generate_youtube_metadata(
    video_job: Any,
) -> GeneratedYouTubeMetadata:
    package_path = (
        Path(video_job.output_directory)
        / "package.json"
    )

    if not package_path.is_file():
        raise FileNotFoundError(
            f"Video package not found: {package_path}"
        )

    package = json.loads(
        package_path.read_text(encoding="utf-8")
    )

    title = _clean_inline(
        package.get("title")
        or video_job.title
    )
    script = str(package.get("script") or "").strip()
    scenes = package.get("scenes") or []
    style = _clean_inline(
        package.get("detected_style")
        or package.get("requested_style")
        or "educational"
    )
    keywords = _collect_keywords(scenes)
    stock_credits = build_stock_credits(scenes)
    compact_stock_credits = build_compact_stock_credits(
        scenes
    )

    prompt = f"""
Create accurate, SEO-focused YouTube metadata for this completed video.

Return one valid JSON object only with these exact keys:
"title", "description", "tags", "category_id", "language"

Rules:
- Base every claim only on the supplied title and script.
- Do not invent facts, statistics, quotations, links, or sources.
- Copy every number, percentage, date, quantity, and century exactly
  from the script. Never rewrite or convert its numeric format.
- Title: natural and compelling, ideally 55-70 characters,
  absolute maximum 100 characters.
- Put the main search topic near the beginning of the title.
- Avoid misleading clickbait, emojis, ALL CAPS, and excessive punctuation.
- Description: 300-450 useful words, while remaining under the
  platform limit after compact media credits are added.
- The first two sentences must clearly explain the video and naturally
  include its primary search topic.
- Include what viewers will learn, important subtopics covered,
  relevant context, and a natural reason to watch.
- Use related search phrases naturally, without keyword stuffing.
- Use readable paragraphs and relevant keyword variations naturally.
- Do not add media credits; the system adds verified credits separately.
- Do not add fake timestamps or chapter markers.
- Tags: JSON array containing 8-18 relevant search phrases.
- Avoid duplicates, unrelated trending terms, and keyword stuffing.
- category_id must be one of the allowed category IDs below.
- language must be an ISO 639-1 code, normally "en".

Allowed YouTube categories:
{CATEGORY_LABELS}

Original title:
{title}

Detected style:
{style}

Useful scene keywords:
{json.dumps(keywords, ensure_ascii=False)}

Validated script:
{_script_excerpt(script)}
""".strip()

    try:
        result = call_ai(prompt, json_mode=True)
        data = _extract_json_object(result.text)

        generated_title = _truncate_title(
            data.get("title") or title
        )
        generated_description = str(
            data.get("description") or ""
        ).strip()

        description_word_count = len(
            generated_description.split()
        )

        if description_word_count < 300:
            expansion_prompt = f"""
Expand the YouTube description below to 300-420 useful words.

Return one valid JSON object only:
{{"description": "..."}}

Rules:
- Preserve every factual claim and every number exactly.
- Do not invent facts, statistics, links, timestamps, or sources.
- Keep the primary topic naturally in the first two sentences.
- Explain what viewers will learn and the important subtopics.
- Use natural related search phrases without keyword stuffing.
- Do not add media credits.
- Do not use emojis or misleading clickbait.

Source title:
{title}

Validated script:
{_script_excerpt(script)}

Current description:
{generated_description}
""".strip()

            expansion_result = call_ai(
                expansion_prompt,
                json_mode=True,
            )
            expansion_data = _extract_json_object(
                expansion_result.text
            )
            expanded_description = str(
                expansion_data.get("description") or ""
            ).strip()

            if len(expanded_description.split()) >= 300:
                generated_description = expanded_description

        _validate_numeric_claims(
            f"{title}\n{script}",
            generated_description,
        )

        if len(generated_description.split()) < 280:
            raise ValueError(
                "AI description remained below 280 words."
            )

        generated_tags = _normalize_tags(
            data.get("tags")
        )
        generated_category = _clean_inline(
            data.get("category_id")
        )
        generated_language = _clean_inline(
            data.get("language") or "en"
        ).lower()

        if generated_category not in CATEGORY_IDS:
            generated_category = _fallback_category(
                title,
                script,
            )

        if not generated_tags:
            generated_tags = _normalize_tags(
                [title, *keywords]
            )

        final_description = merge_description_and_credits(
            generated_description,
            compact_stock_credits,
        )

        validated = validate_youtube_metadata(
            title=generated_title,
            description=final_description,
            tags=",".join(generated_tags),
            category_id=generated_category,
            language=generated_language,
            privacy_status="private",
        )

        return GeneratedYouTubeMetadata(
            title=validated.title,
            description=validated.description,
            tags=validated.tags,
            category_id=validated.category_id,
            language=validated.language,
            provider=result.provider,
            stock_credits=stock_credits,
        )

    except Exception:
        return _fallback_metadata(
            package,
            stock_credits,
        )
