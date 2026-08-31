import logging
from pathlib import Path
import httpx

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
    # AI scene media is preserved for later but currently disabled.
    return "stock_only"


def provider_order(cycle, scene_number=None):
    """Compatibility helper: routing is fixed and no longer rotates by scene."""
    return list(dict.fromkeys(cycle))


def _provider_name(provider):
    return provider.__name__.removesuffix("_image").removesuffix("_video")


# A result at or above this score (or any subject match at the lower bar) ends
# the search immediately; anything weaker is kept only as best-so-far while the
# remaining variants and providers get a chance to beat it.
GOOD_SCORE = 30
GOOD_ENTITY_SCORE = 24
# How many additional downloaded candidates a weak scene may collect before we
# settle for the best seen. Bounds quota and download cost per scene.
MAX_EXTRA_CANDIDATES = 3


def _result_score(result):
    score = result.get("relevance_score")
    return score if isinstance(score, (int, float)) else 0


def _is_good_result(result):
    score = _result_score(result)
    if score >= GOOD_SCORE:
        return True
    return bool(result.get("relevance_entity_match")) and score >= GOOD_ENTITY_SCORE


def try_providers(providers, query, folder, number, extension, orientation,
                  used_sources, topic_context=None):
    """Search providers/variants until a good result appears; otherwise best-of.

    A weak first hit no longer wins by default: the search continues (bounded)
    and the highest-scoring candidate is kept. Candidates download to temp
    names; only the winner is moved onto the scene path.
    """
    final_path = folder / f"scene-{number:03d}.{extension}"
    best = None
    best_path = None
    extra = 0
    for index, provider in enumerate(providers):
        queries = [query] if provider in {pixabay_video, pixabay_image} else provider_query_candidates(query, topic_context)
        provider_name = provider.__name__
        provider_rate_limited = False
        for provider_query in queries:
            path = folder / f"scene-{number:03d}-cand{extra}.{extension}" if best else final_path
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
            if _is_good_result(result):
                if best_path is not None and path != final_path:
                    Path(path).replace(final_path)
                    result["media_file"] = str(final_path)
                _discard_candidate_files(best_path, final_path)
                logger.info(
                    "MEDIA scene=%d provider=%s query=%r result=selected score=%s source=%s",
                    number, provider_name, provider_query,
                    result.get("relevance_score", "provider"), result.get("source", ""),
                )
                return result
            if best is None or _result_score(result) > _result_score(best):
                if best_path is not None:
                    _discard_candidate_files(best_path, final_path)
                best, best_path = result, Path(path)
            else:
                _discard_candidate_files(Path(path), final_path)
            extra += 1
            logger.info(
                "MEDIA scene=%d provider=%s query=%r result=weak score=%s continuing=%s",
                number, provider_name, provider_query,
                result.get("relevance_score"), extra <= MAX_EXTRA_CANDIDATES,
            )
            if extra > MAX_EXTRA_CANDIDATES:
                return _finish_best(best, best_path, final_path, number)
        if provider_rate_limited:
            continue
        logger.info(
            "MEDIA scene=%d provider=%s query=%r result=exhausted",
            number, provider_name, query,
        )
    return _finish_best(best, best_path, final_path, number)


def _discard_candidate_files(path, final_path):
    if path is None or path == final_path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _finish_best(best, best_path, final_path, number):
    if best is None:
        return None
    if best_path is not None and best_path != final_path:
        try:
            best_path.replace(final_path)
        except OSError as error:
            logger.debug("MEDIA scene=%d best-of move failed: %s", number, error)
            return None
        best["media_file"] = str(final_path)
    logger.info(
        "MEDIA scene=%d result=best_of score=%s source=%s",
        number, best.get("relevance_score"), best.get("source", ""),
    )
    return best


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


def _stock_pass(query, folder, number, requested, orientation, excluded, topic_context):
    if requested in {"video", "stock_video"}:
        result = try_providers(
            VIDEO_PROVIDERS, query, folder, number, "mp4", orientation, excluded, topic_context,
        )
        if result:
            return result
    return try_providers(
        IMAGE_PROVIDERS, query, folder, number, "jpg", orientation, excluded, topic_context,
    )


def _is_weak_result(result):
    if result.get("relevance_entity_match") is True:
        return False
    score = result.get("relevance_score")
    return not isinstance(score, (int, float)) or score < 18


def _stock_with_history_preference(query, folder, number, requested, orientation,
                                   used_sources, provider_used_sources, history_sources,
                                   topic_context):
    """Fresh media is preferred, but a weak fresh pick loses to a strong older one.

    History is an exclusion on the first pass. Only when that pass yields a
    non-subject result do we search again with history allowed (same-job
    duplicates still excluded), keeping whichever candidate scores higher.
    """
    result = _stock_pass(query, folder, number, requested, orientation, provider_used_sources, topic_context)
    if not result or not history_sources or not _is_weak_result(result):
        return result
    alt_folder = folder / "history-alt"
    alt_folder.mkdir(parents=True, exist_ok=True)
    try:
        alternative = _stock_pass(
            query, alt_folder, number, requested, orientation, set(used_sources), topic_context,
        )
    except Exception as error:  # never let the retry break the normal path
        logger.debug("MEDIA scene=%d history-preference retry failed: %s", number, error)
        alternative = None
    alt_score = (alternative or {}).get("relevance_score")
    if alternative and isinstance(alt_score, (int, float)) and alt_score > (result.get("relevance_score") or 0):
        alt_path = Path(alternative["media_file"])
        final_path = folder / alt_path.name
        try:
            alt_path.replace(final_path)
        except OSError as error:
            logger.debug("MEDIA scene=%d history-preference move failed: %s", number, error)
            return result
        alternative["media_file"] = str(final_path)
        alternative["history_reuse"] = True
        logger.info(
            "MEDIA scene=%d history-preference=reused_older score=%s over fresh score=%s",
            number, alt_score, result.get("relevance_score"),
        )
        return alternative
    if alternative:
        try:
            Path(alternative["media_file"]).unlink(missing_ok=True)
        except OSError:
            pass
    return result


def select_media(query, folder, number, requested, orientation, used_sources=None,
                 ai_prompt=None, media_source="stock_only", prefer_wikimedia=False,
                 topic_context: MediaTopicContext | None = None, history_sources=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    used_sources = set(used_sources or ())
    provider_used_sources = used_sources | set(history_sources or ())
    if prefer_wikimedia:
        result = _try_licensed_historical_sources(
            query, folder, number, orientation, provider_used_sources,
            history_sources, topic_context,
        )
        if result:
            return result

    result = _stock_with_history_preference(
        query, folder, number, requested, orientation,
        used_sources, provider_used_sources, history_sources, topic_context,
    )
    if result and not _is_weak_result(result):
        return result
    if not prefer_wikimedia:
        # Commons often has the branded/entity subjects stock libraries lack
        # (storefronts, products, race teams). Last resort for weak scenes only.
        try:
            licensed = _try_licensed_historical_sources(
                query, folder, number, orientation, provider_used_sources,
                history_sources, topic_context,
            )
        except Exception as error:
            logger.debug("MEDIA scene=%d licensed fallback failed: %s", number, error)
            licensed = None
        if licensed and _result_score(licensed) > (_result_score(result) if result else 0):
            logger.info(
                "MEDIA scene=%d result=licensed_fallback score=%s source=%s",
                number, licensed.get("relevance_score"), licensed.get("source", ""),
            )
            return licensed
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
