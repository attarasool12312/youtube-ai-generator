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
    prefer_wikimedia=False,
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

    # Historical profiles prefer exact archival imagery from
    # Wikimedia Commons before falling back to generic stock media.
    if prefer_wikimedia:
        try:
            result = download_image(
                query,
                folder / f"scene-{number:03d}.jpg",
                orientation,
                used_sources=used_sources,
                offset=number - 1,
            )

            if result:
                source = result.get("source")

                if source:
                    used_sources.add(source)

                return result

        except Exception as error:
            print(
                f"Wikimedia unavailable for scene {number}: {error}",
                flush=True,
            )

    # Historical Mixed mode:
    # relevant Wikimedia first, then AI rather than unrelated stock.
    # Stock remains the final resilience fallback only if AI fails.
    if prefer_wikimedia and media_source == "mixed":
        try:
            return flux_image(
                ai_prompt or query,
                folder,
                number,
                orientation,
            )
        except Exception as error:
            print(
                f"Historical AI fallback unavailable for scene "
                f"{number}: {error}",
                flush=True,
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
