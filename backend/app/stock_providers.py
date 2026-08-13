import logging
from pathlib import Path
import re

import httpx

from app.api_settings import get_api_key
from app.openverse_client import openverse_headers
from app.media_relevance import build_media_query_candidates, score_media_candidate

CLIENT = httpx.Client(timeout=60, follow_redirects=True)
logger = logging.getLogger(__name__)
MAX_SCORED_CANDIDATES = 30


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
            limit=4,
        )
    return [query]


def pixabay_query_candidates(query, topic_context=None):
    normalized = normalized_query(query)
    words = normalized.split()

    candidates = [
        " ".join(words[:4]),
        " ".join(words[:2]),
    ]
    if topic_context is not None:
        topic_words = normalized_query(topic_context.primary_topic).split()
        candidates = [" ".join(topic_words[:4]), " ".join(topic_words[:2])] + candidates

    return [
        candidate
        for index, candidate in enumerate(candidates)
        if candidate and len(candidate) <= 60 and len(candidate.split()) <= 4
        and candidate not in candidates[:index]
    ]


def _provider_metadata(provider, item):
    if provider == "pexels":
        return item.get("alt") or item.get("url", ""), "", ()
    if provider == "pixabay":
        return str(item.get("tags") or ""), "", str(item.get("tags") or "").split(",")
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
        if not relevance.accepted:
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
        })
    return fields


def try_save_media(url, output_path, headers=None):
    try:
        save_media(url, output_path, headers=headers)
        return True
    except Exception as error:
        logger.debug("Media candidate download rejected url=%r reason=%s", url, error)
        return False


def save_media(url, output_path, headers=None):
    response = CLIENT.get(url, headers=headers)
    response.raise_for_status()

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return str(path)


def pexels_video(query, output_path, orientation, used_sources=None, offset=0, topic_context=None):
    response = CLIENT.get(
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
        response = CLIENT.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": get_api_key("pixabay_api_key"),
                "q": candidate,
                "per_page": 50,
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
    response = CLIENT.get(
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
    response = CLIENT.get(
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
        response = CLIENT.get(
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
                "per_page": 50,
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
    response = CLIENT.get(
        "https://api.openverse.org/v1/images/",
        headers=openverse_headers(),
        params={"q": query, "page_size": 20, "mature": "false"},
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
