import logging
from pathlib import Path
import httpx

from app.ai_media import flux_image
from app.media_relevance import MediaTopicContext
from app.openverse_media import download_historical_image
from app.stock_providers import (
    pexels_video, pixabay_video, coverr_video,
    pexels_image, pixabay_image, openverse_image,
    provider_query_candidates,
)
from app.wikimedia_media import download_image

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
VIDEO_PROVIDERS = (pexels_video, pixabay_video, coverr_video)
IMAGE_PROVIDERS = (pexels_image, pixabay_image, openverse_image)
LICENSED_HISTORICAL_PROVIDERS = (
    ("wikimedia", download_image),
    ("historical_openverse", download_historical_image),
)


def normalize_media_source(value):
    return "ai_only" if value == "ai_only" else "stock_only"


def provider_order(cycle, scene_number=None):
    """Compatibility helper: routing is fixed and no longer rotates by scene."""
    return list(dict.fromkeys(cycle))


def _provider_name(provider):
    return provider.__name__.removesuffix("_image").removesuffix("_video")


def try_providers(providers, query, folder, number, extension, orientation,
                  used_sources, topic_context=None):
    for index, provider in enumerate(providers):
        queries = [query] if provider in {pixabay_video, pixabay_image} else provider_query_candidates(query, topic_context)
        provider_name = provider.__name__
        provider_rate_limited = False
        for provider_query in queries:
            path = folder / f"scene-{number:03d}.{extension}"
            try:
                result = provider(
                    provider_query, path, orientation, used_sources=used_sources,
                    offset=index, topic_context=topic_context,
                )
            except Exception as error:
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
                    retry_after = error.response.headers.get("Retry-After")
                    logger.info(
                        "MEDIA scene=%d provider=%s result=rate_limited status=429 retry_after=%s",
                        number, _provider_name(provider), retry_after or "not_exposed",
                    )
                    provider_rate_limited = True
                    break
                logger.debug(
                    "MEDIA candidate scene=%d provider=%s query=%r rejected=%s",
                    number, provider_name, provider_query, error,
                )
                continue
            if not result:
                continue
            result["media_file"] = str(path)
            result.setdefault("matched_query", provider_query)
            logger.info(
                "MEDIA scene=%d provider=%s query=%r result=selected score=%s source=%s",
                number, provider_name, provider_query,
                result.get("relevance_score", "provider"), result.get("source", ""),
            )
            return result
        if provider_rate_limited:
            continue
        logger.info(
            "MEDIA scene=%d provider=%s query=%r result=exhausted",
            number, provider_name, query,
        )
    return None


def _try_licensed_historical_sources(query, folder, number, orientation,
                                     used_sources, history_sources, topic_context):
    for provider_name, provider in LICENSED_HISTORICAL_PROVIDERS:
        try:
            result = provider(
                query, folder / f"scene-{number:03d}.jpg", orientation,
                used_sources=used_sources, offset=number - 1,
                topic_context=topic_context, history_sources=history_sources,
            )
        except Exception as error:
            logger.debug("MEDIA historical candidate scene=%d provider=%s rejected=%s", number, provider_name, error)
            logger.info("MEDIA scene=%d provider=%s query=%r result=exhausted", number, provider_name, query)
            continue
        if result:
            result.setdefault("matched_query", query)
            logger.info(
                "MEDIA scene=%d provider=%s query=%r result=selected score=%s source=%s",
                number, provider_name, query, result.get("relevance_score", "provider"), result.get("source", ""),
            )
            return result
    return None


def select_media(query, folder, number, requested, orientation, used_sources=None,
                 ai_prompt=None, media_source="stock_only", prefer_wikimedia=False,
                 topic_context: MediaTopicContext | None = None, history_sources=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    used_sources = set(used_sources or ())
    media_source = normalize_media_source(media_source)
    if media_source == "ai_only":
        logger.info("MEDIA scene=%d provider=ai query=%r result=selected", number, ai_prompt or query)
        return flux_image(ai_prompt or query, folder, number, orientation)

    provider_used_sources = used_sources | set(history_sources or ())
    if prefer_wikimedia:
        result = _try_licensed_historical_sources(
            query, folder, number, orientation, provider_used_sources,
            history_sources, topic_context,
        )
        if result:
            return result

    if requested in {"video", "stock_video"}:
        result = try_providers(
            VIDEO_PROVIDERS, query, folder, number, "mp4", orientation,
            provider_used_sources, topic_context,
        )
        if result:
            return result

    result = try_providers(
        IMAGE_PROVIDERS, query, folder, number, "jpg", orientation,
        provider_used_sources, topic_context,
    )
    if result:
        return result

    if not prefer_wikimedia:
        result = _try_licensed_historical_sources(
            query, folder, number, orientation, provider_used_sources,
            history_sources, topic_context,
        )
        if result:
            return result

    logger.info("MEDIA scene=%d result=stock_exhausted query=%r", number, query)
    raise RuntimeError(f"Scene {number} stock exhausted after normal providers and emergency topic/reuse fallback.")
