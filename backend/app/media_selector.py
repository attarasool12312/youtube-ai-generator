import logging
from pathlib import Path
import time
import httpx

from app.media_relevance import MediaTopicContext
from app.openverse_media import download_historical_image
from app.stock_providers import (
    pexels_video, pixabay_video, coverr_video,
    pexels_image, pixabay_image, provider_query_candidates,
)
from app.wikimedia_media import download_image

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Final stock policy: motion footage first.  Openverse/Wikimedia are NOT normal
# providers; they are reserved for scenes that explicitly request an archival
# still/map/document/diagram in a historical profile.
VIDEO_PROVIDERS = (pexels_video, pixabay_video, coverr_video)
IMAGE_PROVIDERS = (pexels_image, pixabay_image)
LICENSED_HISTORICAL_PROVIDERS = (
    ("wikimedia", download_image),
    ("historical_openverse", download_historical_image),
)

VIDEO_ACCEPT_SCORE = 80
IMAGE_ACCEPT_SCORE = 80
SCENE_STOCK_BUDGET_SECONDS = 8.0
MAX_EXTRA_CANDIDATES = 3
CONFIDENCE_ORDER = {"bad": 0, "weak": 1, "acceptable": 2, "strong": 3}


def normalize_media_source(value):
    # Z-Image will be wired behind needs_ai_fallback in the next module.
    return "stock_only"


def provider_order(cycle, scene_number=None):
    return list(dict.fromkeys(cycle))


def _provider_name(provider):
    return provider.__name__.removesuffix("_image").removesuffix("_video")


def _result_score(result):
    score = (result or {}).get("relevance_score")
    return score if isinstance(score, (int, float)) else 0


def _result_confidence(result):
    value = str((result or {}).get("relevance_confidence") or "").lower()
    if value in CONFIDENCE_ORDER:
        return value
    score = _result_score(result)
    if score >= VIDEO_ACCEPT_SCORE:
        return "strong"
    if score >= 55:
        return "acceptable"
    if score >= 25:
        return "weak"
    return "bad"


def _meets_threshold(result, minimum_score=VIDEO_ACCEPT_SCORE):
    return bool(
        result
        and _result_confidence(result) == "strong"
        and _result_score(result) >= minimum_score
        and not (result.get("relevance_penalties") or [])
    )


def _is_good_result(result):
    return _meets_threshold(result, VIDEO_ACCEPT_SCORE)


def _verified_acceptable_result(result):
    """Allow only unusually well-proven acceptable stock below strong threshold.

    This preserves genuinely relevant common stock while preventing generic
    55-score matches from bypassing Z-Image.
    """
    if not result or (result.get("relevance_penalties") or []):
        return False

    if _meets_threshold(result, VIDEO_ACCEPT_SCORE):
        return True

    return bool(
        _result_confidence(result) == "acceptable"
        and _result_score(result) >= 60
        and float(result.get("subject_coverage") or 0) >= 1.0
        and float(result.get("descriptive_coverage") or 0) >= 1.0
    )


def _better_result(first, second):
    if not first:
        return second
    if not second:
        return first
    first_key = (CONFIDENCE_ORDER[_result_confidence(first)], _result_score(first))
    second_key = (CONFIDENCE_ORDER[_result_confidence(second)], _result_score(second))
    return second if second_key > first_key else first


def _discard_result_media(result):
    if not result:
        return
    try:
        Path(result.get("media_file") or "").unlink(missing_ok=True)
    except (OSError, TypeError):
        pass


def _adopt_result_media(result, folder, number):
    if not result:
        return result
    source_path = Path(result.get("media_file") or "")
    if not source_path.is_file():
        return result
    suffix = source_path.suffix or ".jpg"
    final_path = Path(folder) / f"scene-{number:03d}{suffix}"
    if source_path != final_path:
        final_path.unlink(missing_ok=True)
        source_path.replace(final_path)
        result["media_file"] = str(final_path)
    return result


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
            final_path.unlink(missing_ok=True)
            best_path.replace(final_path)
        except OSError as error:
            logger.debug("MEDIA scene=%d best-of move failed: %s", number, error)
            return None
        best["media_file"] = str(final_path)
    logger.info(
        "MEDIA scene=%d result=best_of score=%s confidence=%s source=%s",
        number, best.get("relevance_score"), _result_confidence(best), best.get("source", ""),
    )
    return best


def try_providers(providers, query, folder, number, extension, orientation,
                  used_sources, topic_context=None, deadline=None):
    """Search one exact query across providers, video-first and bounded.

    The scene-AI query is already the semantic search plan.  Provider-specific
    broadening here previously multiplied API calls and changed word sense.  We
    now search the exact query once per provider, rank each provider internally,
    and stop only on a genuinely strong >=80 result.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    final_path = folder / f"scene-{number:03d}.{extension}"
    deadline = deadline or (time.monotonic() + SCENE_STOCK_BUDGET_SECONDS)
    best = None
    best_path = None
    candidate_index = 0

    for index, provider in enumerate(providers):
        if time.monotonic() >= deadline:
            logger.info("MEDIA scene=%d result=search_budget_exhausted query=%r", number, query)
            break
        path = final_path if best is None else folder / f"scene-{number:03d}-cand{candidate_index}.{extension}"
        try:
            result = provider(
                query, path, orientation, used_sources=used_sources,
                offset=index, topic_context=topic_context,
            )
        except Exception as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
                logger.info(
                    "MEDIA scene=%d provider=%s result=rate_limited status=429",
                    number, _provider_name(provider),
                )
            else:
                logger.debug(
                    "MEDIA candidate scene=%d provider=%s query=%r rejected=%s",
                    number, _provider_name(provider), query, error,
                )
            continue
        if not result:
            continue

        result["media_file"] = str(path)
        result.setdefault("matched_query", query)
        candidate_index += 1

        if _meets_threshold(result, VIDEO_ACCEPT_SCORE):
            if path != final_path:
                final_path.unlink(missing_ok=True)
                Path(path).replace(final_path)
                result["media_file"] = str(final_path)
            _discard_candidate_files(best_path, final_path)
            logger.info(
                "MEDIA scene=%d provider=%s query=%r result=accepted score=%s source=%s",
                number, _provider_name(provider), query,
                result.get("relevance_score"), result.get("source", ""),
            )
            return result

        if best is None or _better_result(best, result) is result:
            if best_path is not None:
                _discard_candidate_files(best_path, final_path)
            best, best_path = result, Path(path)
        else:
            _discard_candidate_files(Path(path), final_path)

        logger.info(
            "MEDIA scene=%d provider=%s query=%r result=below_threshold score=%s confidence=%s",
            number, _provider_name(provider), query,
            result.get("relevance_score"), _result_confidence(result),
        )

    return _finish_best(best, best_path, final_path, number)


def _first_search_variant(query, topic_context):
    if topic_context is None:
        return ""
    normalized_query = " ".join(str(query or "").lower().split())
    for value in getattr(topic_context, "scene_search_variants", ()) or ():
        value = " ".join(str(value or "").split()).strip()
        if value and value.lower() != normalized_query:
            return value
    return ""


def _search_exact_then_one_variant(providers, query, folder, number, extension,
                                   orientation, excluded, topic_context, deadline):
    first = try_providers(
        providers, query, folder, number, extension, orientation, excluded,
        topic_context, deadline=deadline,
    )
    if _meets_threshold(first, VIDEO_ACCEPT_SCORE):
        return first

    variant = _first_search_variant(query, topic_context)
    if not variant or time.monotonic() >= deadline:
        return first

    alt_folder = Path(folder) / f"alt-{extension}"
    second = try_providers(
        providers, variant, alt_folder, number, extension, orientation, excluded,
        topic_context, deadline=deadline,
    )
    winner = _better_result(first, second)
    loser = second if winner is first else first
    _discard_result_media(loser)
    if winner is second:
        winner = _adopt_result_media(winner, folder, number)
    return winner


def _try_licensed_historical_sources(query, folder, number, orientation,
                                     used_sources, history_sources, topic_context):
    for provider_name, provider in LICENSED_HISTORICAL_PROVIDERS:
        try:
            result = provider(
                query, Path(folder) / f"scene-{number:03d}.jpg", orientation,
                used_sources=used_sources, offset=number - 1,
                topic_context=topic_context, history_sources=history_sources,
            )
        except Exception as error:
            logger.debug("MEDIA historical candidate scene=%d provider=%s rejected=%s", number, provider_name, error)
            continue
        if result:
            result.setdefault("matched_query", query)
            return result
    return None


def _stock_pass(query, folder, number, requested, orientation, excluded, topic_context):
    """Always search video first. Images are an explicit-still fallback only."""
    deadline = time.monotonic() + SCENE_STOCK_BUDGET_SECONDS

    video_result = _search_exact_then_one_variant(
        VIDEO_PROVIDERS, query, folder, number, "mp4", orientation, excluded,
        topic_context, deadline,
    )
    if _verified_acceptable_result(video_result):
        video_result["needs_ai_fallback"] = False
        video_result["stock_decision"] = (
            "video_accepted_strong"
            if _meets_threshold(video_result, VIDEO_ACCEPT_SCORE)
            else "video_accepted_verified"
        )
        return video_result

    # Normal scenes remain video-first even if the AI mistakenly requested an
    # image.  Only explicit still-type scenes may use Pexels/Pixabay photos.
    explicit_still = requested in {"image", "stock_image"}
    image_result = None
    if explicit_still and time.monotonic() < deadline:
        image_result = _search_exact_then_one_variant(
            IMAGE_PROVIDERS, query, folder, number, "jpg", orientation, excluded,
            topic_context, deadline,
        )
        if _verified_acceptable_result(image_result):
            _discard_result_media(video_result)
            image_result["needs_ai_fallback"] = False
            image_result["stock_decision"] = (
                "explicit_still_accepted_strong"
                if _meets_threshold(image_result, IMAGE_ACCEPT_SCORE)
                else "explicit_still_accepted_verified"
            )
            return image_result

    # Keep the best below-threshold candidate only long enough for diagnostics.
    # select_media will discard it and trigger the bounded broad-query pass;
    # if that also fails, scene_media hands the scene to Z-Image.
    winner = _better_result(video_result, image_result)
    loser = image_result if winner is video_result else video_result
    _discard_result_media(loser)
    if winner:
        winner["needs_ai_fallback"] = True
        winner["stock_decision"] = "ai_fallback_pending"
    return winner


def _is_weak_result(result):
    return not _meets_threshold(result, VIDEO_ACCEPT_SCORE)


def _stock_with_history_preference(query, folder, number, requested, orientation,
                                   used_sources, provider_used_sources, history_sources,
                                   topic_context):
    return _stock_pass(
        query, folder, number, requested, orientation, set(used_sources), topic_context,
    )


def select_media(query, folder, number, requested, orientation, used_sources=None,
                 ai_prompt=None, media_source="stock_only", prefer_wikimedia=False,
                 topic_context: MediaTopicContext | None = None, history_sources=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    used_sources = set(used_sources or ())
    provider_used_sources = used_sources | set(history_sources or ())

    result = _stock_with_history_preference(
        query, folder, number, requested, orientation,
        used_sources, provider_used_sources, history_sources, topic_context,
    )
    if result and _verified_acceptable_result(result) and not result.get("needs_ai_fallback"):
        return result

    # Wikimedia/Openverse are no longer a generic fallback.  They may compete
    # only for an explicitly requested historical still, which prevents modern
    # explainer videos from turning into old-image slideshows.
    if prefer_wikimedia and requested in {"image", "stock_image"} and topic_context is not None:
        licensed_folder = folder / "licensed-historical"
        licensed_folder.mkdir(parents=True, exist_ok=True)
        licensed = _try_licensed_historical_sources(
            query, licensed_folder, number, orientation, provider_used_sources,
            history_sources, topic_context,
        )
        if _meets_threshold(licensed, IMAGE_ACCEPT_SCORE):
            _discard_result_media(result)
            licensed = _adopt_result_media(licensed, folder, number)
            licensed["needs_ai_fallback"] = False
            licensed["stock_decision"] = "historical_still_accepted"
            return licensed
        _discard_result_media(licensed)

    if result:
        logger.info(
            "MEDIA scene=%d result=below_strong_threshold score=%s confidence=%s query=%r action=try_broad_or_ai",
            number, _result_score(result), _result_confidence(result), query,
        )
        _discard_result_media(result)

    logger.info("MEDIA scene=%d result=stock_exhausted query=%r", number, query)
    raise RuntimeError(f"Scene {number} stock exhausted after bounded video-first search.")
