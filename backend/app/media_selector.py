from pathlib import Path

from app.ai_media import flux_image

from app.stock_providers import (
    pexels_video, pixabay_video, coverr_video,
    pexels_image, pixabay_image, openverse_image,
)
from app.wikimedia_media import download_image

VIDEO_CYCLE = [
    pexels_video, pixabay_video,
    pexels_video, pixabay_video, coverr_video,
]

IMAGE_CYCLE = [
    pexels_image, pixabay_image,
    pexels_image, pixabay_image, openverse_image,
]


def provider_order(cycle, scene_number):
    start = (scene_number - 1) % len(cycle)
    rotated = cycle[start:] + cycle[:start]

    ordered = []
    for provider in rotated:
        if provider not in ordered:
            ordered.append(provider)

    return ordered


def try_providers(
    providers, query, folder, number,
    extension, orientation, used_sources,
):
    for index, provider in enumerate(providers):
        path = folder / f"scene-{number:03d}.{extension}"

        try:
            result = provider(
                query,
                path,
                orientation,
                used_sources=used_sources,
                offset=(number * 3) + index,
            )
            if not result:
                continue
            result["media_file"] = str(path)

            source = result.get("source")
            if source:
                used_sources.add(source)

            return result
        except Exception as error:
            print(f"{provider.__name__} failed: {error}")
            continue

    return None


def select_media(
    query,
    folder,
    number,
    requested,
    orientation,
    used_sources=None,
    ai_prompt=None,
    media_source="mixed",
):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if used_sources is None:
        used_sources = set()

    if media_source not in {"stock_only", "ai_only", "mixed"}:
        media_source = "mixed"

    if media_source == "ai_only":
        return flux_image(
            ai_prompt or query,
            folder,
            number,
            orientation,
        )

    use_video = requested == "video" and number % 4 != 0
    if use_video:
        result = try_providers(
            provider_order(VIDEO_CYCLE, number),
            query, folder, number, "mp4",
            orientation, used_sources,
        )
        if result:
            return result

    result = try_providers(
        provider_order(IMAGE_CYCLE, number),
        query, folder, number, "jpg",
        orientation, used_sources,
    )
    if result:
        return result
    if use_video:
        result = try_providers(
            provider_order(VIDEO_CYCLE, number),
            query, folder, number, "mp4",
            orientation, used_sources,
        )
        if result:
            return result

    result = try_providers(
        provider_order(IMAGE_CYCLE, number),
        query, folder, number, "jpg",
        orientation, used_sources,
    )
    if result:
        return result

    if media_source == "mixed":
        return flux_image(
            ai_prompt or query,
            folder,
            number,
            orientation,
        )

    raise RuntimeError("All stock media providers failed.")
