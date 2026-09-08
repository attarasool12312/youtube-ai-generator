import json
import logging
import subprocess
from pathlib import Path
import re

import httpx

from app.api_settings import get_api_key
from app.openverse_client import openverse_headers
from app.media_relevance import build_media_query_candidates, concrete_subject_words, practical_head_query, score_media_candidate

SEARCH_CLIENT = httpx.Client(timeout=httpx.Timeout(1.5, connect=1.0), follow_redirects=True)
DOWNLOAD_CLIENT = httpx.Client(timeout=httpx.Timeout(4.0, connect=1.5), follow_redirects=True)
logger = logging.getLogger(__name__)
MAX_SCORED_CANDIDATES = 10


def orientation_ok(width, height, orientation):
    if width <= 0 or height <= 0:
        return False

    ratio = width / height

    if orientation == "portrait":
        return ratio <= 0.80
    if orientation == "square":
        return 0.85 <= ratio <= 1.18
    return ratio >= 1.25


def rotate_items(items, offset):
    items = list(items)
    if not items:
        return []
    offset %= len(items)
    return items[offset:] + items[:offset]


def normalized_query(query):
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", " ", query)
    words = []

    for word in cleaned.lower().split():
        if word not in words:
            words.append(word)

    return " ".join(words)[:100].strip()


def rank_pixabay_items(items, query, offset):
    terms = {
        word
        for word in normalized_query(query).split()
        if len(word) > 2
    }

    def score(item):
        tags = {
            word
            for word in re.findall(
                r"[a-zA-Z0-9]+",
                str(item.get("tags") or "").lower(),
            )
            if len(word) > 2
        }

        return (
            len(terms.intersection(tags)),
            int(bool(terms) and terms.issubset(tags)),
            int(item.get("likes") or 0),
            int(item.get("downloads") or 0),
            int(item.get("views") or 0),
        )

    ranked = sorted(items, key=score, reverse=True)
    preferred = rotate_items(ranked[:20], offset)
    return preferred + ranked[20:]


def pixabay_minimum_dimensions(orientation):
    if orientation == "portrait":
        return 720, 1280
    if orientation == "square":
        return 900, 900
    return 1280, 720


def pixabay_image_orientation(orientation):
    if orientation == "portrait":
        return "vertical"
    if orientation == "landscape":
        return "horizontal"
    return "all"


def provider_query_candidates(query, topic_context=None):
    if topic_context is not None:
        return build_media_query_candidates(
            query, topic_context,
            limit=2,
        )
    return [query]


def pixabay_query_candidates(query, topic_context=None):
    """Tightly bound Pixabay broadening because its tag matching is loose."""
    candidates = build_media_query_candidates(query, topic_context, limit=2)
    if len(candidates) < 2:
        head = practical_head_query(query, topic_context)
        if head:
            candidates.append(head)
    result = []
    for candidate in candidates:
        candidate = " ".join(str(candidate or "").split()).strip()
        if not candidate or len(candidate) > 60 or len(candidate.split()) > 5:
            continue
        if candidate not in result:
            result.append(candidate)
        if len(result) >= 2:
            break
    return result


def _pixabay_descriptive_title(item):
    page_url = str(item.get("pageURL") or "")
    match = re.search(r"/(?:photos|illustrations|vectors|videos)/([^/?#]+)/?$", page_url)
    if not match:
        return ""
    slug = re.sub(r"-\d+$", "", match.group(1)).strip("-")
    if not slug or slug.startswith("id-"):
        return ""
    return slug.replace("-", " ")


def _provider_metadata(provider, item):
    if provider == "pexels":
        return item.get("alt") or item.get("url", ""), "", ()
    if provider == "pixabay":
        tags = str(item.get("tags") or "")
        return _pixabay_descriptive_title(item), "", [value.strip() for value in tags.split(",") if value.strip()]
    if provider == "coverr":
        return item.get("title") or item.get("slug", ""), item.get("description", ""), item.get("tags") or ()
    return item.get("title", ""), item.get("description", ""), item.get("tags") or ()


def rank_provider_items(items, provider, query, topic_context, used_sources, offset=0):
    items = list(items)[:MAX_SCORED_CANDIDATES]
    if topic_context is None:
        return rotate_items(items, offset)
    ranked = []
    for item in items:
        source = item.get("url") or item.get("pageURL") or item.get("foreign_landing_url") or ""
        asset_id = item.get("id") or item.get("slug")
        asset_key = f"{provider}:{asset_id}" if asset_id not in (None, "") else ""
        if source in (used_sources or set()) or asset_key in (used_sources or set()):
                continue
        title, description, tags = _provider_metadata(provider, item)
        relevance = score_media_candidate(
            topic_context, query, title=str(title or ""),
            description=str(description or ""), tags=tags,
        )
        logger.debug(
            "Media candidate provider=%s profile=%s topic=%r query=%r score=%d accepted=%s reason=%s",
            provider, topic_context.profile, topic_context.primary_topic, query,
            relevance.score, relevance.accepted, relevance.reason,
        )
        if not relevance.accepted or (
            relevance.confidence == "bad"
            and relevance.reason != "provider-result-sparse-metadata"
        ):
                continue
        item["_media_relevance"] = relevance
        item["_media_title"] = str(title or "")
        item["_media_tags"] = tags
        item["_asset_key"] = asset_key
        ranked.append(item)
    ranked.sort(key=lambda value: value["_media_relevance"].score, reverse=True)
    if ranked and offset:
        top_score = ranked[0]["_media_relevance"].score
        tied = [item for item in ranked if item["_media_relevance"].score == top_score]
        rest = ranked[len(tied):]
        shift = int(offset) % min(len(tied), 4)
        ranked = tied[shift:] + tied[:shift] + rest
    return ranked


def _relevance_fields(item):
    relevance = item.get("_media_relevance")
    fields = {
        "title": item.get("_media_title", ""),
        "tags": item.get("_media_tags", ()),
        "asset_id": item.get("id") or item.get("slug"),
        "asset_key": item.get("_asset_key", ""),
    }
    if relevance:
        fields.update({
            "relevance_score": relevance.score,
            "relevance_reason": relevance.reason,
            "relevance_entity_match": relevance.entity_match,
            "relevance_context_matches": relevance.context_matches,
                "relevance_confidence": relevance.confidence,
                "relevance_penalties": list(relevance.penalties),
                "subject_coverage": relevance.subject_coverage,
                "descriptive_coverage": relevance.descriptive_coverage,
                "required_matches": relevance.required_matches,
        })
    return fields


_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v"}


def _validate_video_file(path):
    """Reject undecodable or pathological video files before they reach render.

    A provider once returned a "video" whose container was 30 minutes of audio
    with 5 seconds of frames; ffmpeg's -stream_loop then spun for an hour.
    Rules: a decodable video stream must exist, carry at least ~0.5s of frames,
    and the container may not be wildly longer than the video stream.
    """
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,duration,nb_frames,avg_frame_rate",
             "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        if probe.returncode != 0:
            return False, "ffprobe-failed"
        payload = json.loads(probe.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams:
            return False, "no-video-stream"
        stream = streams[0]
        video_duration = _probe_float(stream.get("duration"))
        if video_duration is None:
            frames = _probe_float(stream.get("nb_frames"))
            rate = _probe_rate(stream.get("avg_frame_rate"))
            if frames and rate:
                video_duration = frames / rate
        container_duration = _probe_float((payload.get("format") or {}).get("duration"))
        if video_duration is not None and video_duration < 0.5:
            return False, f"video-stream-too-short:{video_duration:.2f}s"
        if (video_duration is not None and container_duration is not None
                and container_duration > video_duration * 3 + 10):
            return False, f"container-video-mismatch:{container_duration:.0f}s-vs-{video_duration:.0f}s"
        return True, "ok"
    except Exception as error:  # validation must never break downloads
        logger.debug("Video validation skipped for %s: %s", path, error)
        return True, "validation-skipped"


def _probe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _probe_rate(value):
    try:
        numerator, _, denominator = str(value or "").partition("/")
        return float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError):
        return None


def try_save_media(url, output_path, headers=None):
    try:
        save_media(url, output_path, headers=headers)
    except Exception as error:
        logger.debug("Media candidate download rejected url=%r reason=%s", url, error)
        return False
    if Path(output_path).suffix.lower() in _VIDEO_SUFFIXES:
        valid, reason = _validate_video_file(output_path)
        if not valid:
            logger.info("Media candidate rejected url=%r reason=%s", url, reason)
            Path(output_path).unlink(missing_ok=True)
            return False
    return True


def save_media(url, output_path, headers=None):
    response = DOWNLOAD_CLIENT.get(url, headers=headers)
    response.raise_for_status()

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return str(path)


def pexels_video(query, output_path, orientation, used_sources=None, offset=0, topic_context=None):
    response = SEARCH_CLIENT.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": get_api_key("pexels_api_key")},
        params={"query": query, "per_page": 15},
    )
    response.raise_for_status()

    items = rank_provider_items(response.json().get("videos", []), "pexels", query, topic_context, used_sources, offset)
    for item in items:
        source = item.get("url", "")
        if source in (used_sources or set()):
                continue
        files = sorted(
            item.get("video_files", []),
            key=lambda x: (x.get("width") or 0) *
                          (x.get("height") or 0),
            reverse=True,
        )
        for file in files:
            width = file.get("width") or 0
            height = file.get("height") or 0

            if file.get("file_type") != "video/mp4":
                continue
            if not orientation_ok(width, height, orientation):
                continue
            if width * height < 1280 * 720:
                continue
            if max(width, height) > 1920:
                continue
            if not try_save_media(file["link"], output_path):
                continue
            return {
                "provider": "pexels",
                "media_type": "video",
                "source": source,
                "author": item.get("user", {}).get("name", ""),
                "width": width,
                "height": height,
                **_relevance_fields(item),
            }

    raise RuntimeError("No suitable Pexels video found.")


def pixabay_video(query, output_path, orientation, used_sources=None, offset=0, topic_context=None):
    for candidate in pixabay_query_candidates(query, topic_context):
        response = SEARCH_CLIENT.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": get_api_key("pixabay_api_key"),
                "q": candidate,
                "per_page": 30,
                "order": "popular",
                "safesearch": "true",
            },
        )
        response.raise_for_status()

        items = rank_provider_items(response.json().get("hits", []), "pixabay", candidate, topic_context, used_sources, offset)

        for item in items:
            source = item.get("pageURL", "")
            if not source or source in (used_sources or set()):
                continue
            if item.get("isLowQuality"):
                continue

            files = sorted(
                item.get("videos", {}).values(),
                key=lambda file: (
                    (file.get("width") or 0)
                    * (file.get("height") or 0)
                ),
                reverse=True,
            )

            for file in files:
                width = file.get("width") or 0
                height = file.get("height") or 0
                url = file.get("url")

                if not url:
                    continue
                if not orientation_ok(width, height, orientation):
                    continue

                if not try_save_media(url, output_path):
                    continue
                return {
                    "provider": "pixabay",
                    "media_type": "video",
                    "source": source,
                    "author": item.get("user", ""),
                    "width": width,
                    "height": height,
                **_relevance_fields(item),
                    "matched_query": candidate,
                }

    raise RuntimeError("No suitable Pixabay video found.")


def coverr_video(query, output_path, orientation, used_sources=None, offset=0, topic_context=None):
    response = SEARCH_CLIENT.get(
        "https://api.coverr.co/videos",
        headers={"Authorization":
                 f"Bearer {get_api_key('coverr_api_key')}"},
        params={"query": query, "page_size": 15, "urls": "true"},
    )
    response.raise_for_status()
    items = rank_provider_items(response.json().get("hits", []), "coverr", query, topic_context, used_sources, offset)
    for item in items:
        source = (
            "https://coverr.co/videos/"
            + str(item.get("slug", ""))
        )
        if source in (used_sources or set()):
                continue
        if item.get("is_premium"):
                continue

        width = item.get("max_width") or 0
        height = item.get("max_height") or 0
        url = item.get("urls", {}).get("mp4_download")

        if not url or not orientation_ok(width, height, orientation):
                continue

        if not try_save_media(url, output_path):
                continue
        return {
            "provider": "coverr", "media_type": "video",
            "source": source,
            "author": str(item.get("contributor_id") or ""),
            "width": width, "height": height,
            **_relevance_fields(item),
        }

    raise RuntimeError("No suitable Coverr video found.")


def pexels_image(query, output_path, orientation, used_sources=None, offset=0, topic_context=None):
    response = SEARCH_CLIENT.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": get_api_key("pexels_api_key")},
        params={"query": query, "per_page": 15},
    )
    response.raise_for_status()

    items = rank_provider_items(response.json().get("photos", []), "pexels", query, topic_context, used_sources, offset)
    for item in items:
        source = item.get("url", "")
        if source in (used_sources or set()):
                continue
        width, height = item["width"], item["height"]
        if not orientation_ok(width, height, orientation):
                continue
        if not try_save_media(item["src"]["large2x"], output_path):
                continue
        return {
            "provider": "pexels", "media_type": "image",
            "source": source, "author": item["photographer"],
            "width": width, "height": height,
            **_relevance_fields(item),
        }

    raise RuntimeError("No suitable Pexels image found.")


def pixabay_image(query, output_path, orientation, used_sources=None, offset=0, topic_context=None):
    minimum_width, minimum_height = pixabay_minimum_dimensions(
        orientation
    )

    for candidate in pixabay_query_candidates(query, topic_context):
        response = SEARCH_CLIENT.get(
            "https://pixabay.com/api/",
            params={
                "key": get_api_key("pixabay_api_key"),
                "q": candidate,
                "image_type": "photo",
                "orientation": pixabay_image_orientation(
                    orientation
                ),
                "min_width": minimum_width,
                "min_height": minimum_height,
                "per_page": 30,
                "order": "popular",
                "safesearch": "true",
            },
        )
        response.raise_for_status()

        items = rank_provider_items(response.json().get("hits", []), "pixabay", candidate, topic_context, used_sources, offset)

        for item in items:
            source = item.get("pageURL", "")
            if not source or source in (used_sources or set()):
                continue
            if item.get("isLowQuality"):
                continue

            width = item.get("imageWidth") or 0
            height = item.get("imageHeight") or 0
            url = (
                item.get("largeImageURL")
                or item.get("webformatURL")
            )

            if not url:
                continue
            if not orientation_ok(width, height, orientation):
                continue

            if not try_save_media(url, output_path):
                continue
            return {
                "provider": "pixabay",
                "media_type": "image",
                "source": source,
                "author": item.get("user", ""),
                "width": width,
                "height": height,
                **_relevance_fields(item),
                "matched_query": candidate,
            }

    raise RuntimeError("No suitable Pixabay image found.")


def openverse_image(query, output_path, orientation, used_sources=None, offset=0, topic_context=None):
    response = SEARCH_CLIENT.get(
        "https://api.openverse.org/v1/images/",
        headers=openverse_headers(),
        params={"q": query, "page_size": 12, "mature": "false"},
    )
    response.raise_for_status()

    items = rank_provider_items(response.json().get("results", []), "openverse", query, topic_context, used_sources, offset)
    for item in items:
        source = (
            item.get("foreign_landing_url")
            or item.get("url", "")
        )
        if source in (used_sources or set()):
                continue
        width = item.get("width") or 0
        height = item.get("height") or 0
        license_slug = str(item.get("license") or "").strip().lower()
        if license_slug not in {"cc0", "pdm", "by"}:
                continue
        if not item.get("url"):
                continue
        if not orientation_ok(width, height, orientation):
                continue

        if not try_save_media(item["url"], output_path):
                continue
        return {
            "provider": "openverse",
            "media_type": "image",
            "source": source,
            "author": item.get("creator", ""),
            "license": item.get("license", ""),
            "license_url": item.get("license_url", ""),
            "width": width,
            "height": height,
                **_relevance_fields(item),
        }

    raise RuntimeError("No suitable Openverse image found.")
