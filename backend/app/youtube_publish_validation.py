from __future__ import annotations

from dataclasses import dataclass


MAX_TITLE_CHARACTERS = 100
MAX_DESCRIPTION_BYTES = 5000
MAX_TAG_CHARACTERS = 500

ALLOWED_PRIVACY_STATUSES = {
    "private",
    "unlisted",
    "public",
}


class YouTubePublishValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(" ".join(errors))


@dataclass(frozen=True)
class ValidatedYouTubeMetadata:
    title: str
    description: str
    tags: list[str]
    category_id: str
    language: str
    privacy_status: str


def parse_tags(value: str) -> list[str]:
    normalized = value.replace("\n", ",")
    tags: list[str] = []
    seen: set[str] = set()

    for raw_tag in normalized.split(","):
        tag = " ".join(raw_tag.strip().split())

        if not tag:
            continue

        key = tag.casefold()

        if key in seen:
            continue

        seen.add(key)
        tags.append(tag)

    return tags


def calculate_tags_length(tags: list[str]) -> int:
    total = 0

    for index, tag in enumerate(tags):
        if index:
            total += 1

        total += len(tag)

        if " " in tag:
            total += 2

    return total


def validate_youtube_metadata(
    *,
    title: str,
    description: str,
    tags: str,
    category_id: str,
    language: str,
    privacy_status: str,
) -> ValidatedYouTubeMetadata:
    clean_title = " ".join(title.strip().split())
    clean_description = description.strip()
    clean_tags = parse_tags(tags)
    clean_category_id = category_id.strip()
    clean_language = language.strip()
    clean_privacy = privacy_status.strip().lower()

    errors: list[str] = []

    if not clean_title:
        errors.append("Video title is required.")
    elif len(clean_title) > MAX_TITLE_CHARACTERS:
        errors.append(
            "Video title must not exceed "
            f"{MAX_TITLE_CHARACTERS} characters."
        )

    if "<" in clean_title or ">" in clean_title:
        errors.append(
            "Video title cannot contain angle brackets."
        )

    description_bytes = len(
        clean_description.encode("utf-8")
    )

    if description_bytes > MAX_DESCRIPTION_BYTES:
        errors.append(
            "Video description must not exceed "
            f"{MAX_DESCRIPTION_BYTES} UTF-8 bytes."
        )

    if "<" in clean_description or ">" in clean_description:
        errors.append(
            "Video description cannot contain angle brackets."
        )

    tags_length = calculate_tags_length(clean_tags)

    if tags_length > MAX_TAG_CHARACTERS:
        errors.append(
            "Combined tags must not exceed "
            f"{MAX_TAG_CHARACTERS} characters."
        )

    if not clean_category_id.isdigit():
        errors.append("A valid YouTube category is required.")

    if not clean_language:
        errors.append("Video language is required.")

    if clean_privacy not in ALLOWED_PRIVACY_STATUSES:
        errors.append(
            "Privacy must be private, unlisted, or public."
        )

    if errors:
        raise YouTubePublishValidationError(errors)

    return ValidatedYouTubeMetadata(
        title=clean_title,
        description=clean_description,
        tags=clean_tags,
        category_id=clean_category_id,
        language=clean_language,
        privacy_status=clean_privacy,
    )
