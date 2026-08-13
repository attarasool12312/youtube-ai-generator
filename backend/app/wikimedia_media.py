"""Safe, entity-aware Wikimedia Commons image selection."""
from __future__ import annotations

from collections import Counter
import html
from io import BytesIO
import logging
from pathlib import Path
import re
import time

import httpx
from PIL import Image
from app.media_relevance import build_media_query_candidates, normalize_media_text, score_media_candidate
from app.media_relevance import build_media_query_candidates, normalize_media_text


API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {
    "User-Agent": "AttaVideoAI/1.0 (https://attavideoai.site/; admin@attarasool.com)"
}
ALLOWED_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 3
RETRY_DELAYS = (0.25, 0.75)
PAGE_SIZE = 20
MAX_PAGES_PER_QUERY = 2
MAX_API_PAGES = 6
MAX_CANDIDATES = 120
MAX_DOWNLOAD_ATTEMPTS = 8
MIN_FRESH_POOL = 5
MAX_IMAGE_BYTES = 15 * 1024 * 1024
logger = logging.getLogger(__name__)

_GENERIC = frozenset({
    "archival", "archive", "black", "early", "famous", "historic", "historical",
    "history", "image", "late", "old", "older", "photo", "photograph",
    "photography", "picture", "portrait", "the", "of", "and", "in", "with",
    "at", "on",
})
_BANNED_TERMS = (
    "book cover", "catalogue", "catalog of", "frontispiece", "title page",
    "dust jacket", "magazine cover", "album cover",
)


def _value(item):
    return str(item.get("value", "") or "") if isinstance(item, dict) else str(item or "")


def _clean(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _normalized(value):
    return normalize_media_text(value)


def _tokens(value):
    return tuple(token for token in _normalized(value).split() if len(token) > 1)


def _allowed_license(name):
    name = (name or "").upper().strip()
    if "PUBLIC DOMAIN" in name or "CC0" in name:
        return True
    if "CC BY" in name:
        blocked = ("BY-SA", "BY-NC", "BY-ND", "NONCOMMERCIAL", "NO DERIVATIVES")
        return not any(term in name for term in blocked)
    return False


def _query_candidates(query, topic_context=None):
    return build_media_query_candidates(query, topic_context)


def _request(client, url, **kwargs):
    """Retry only transient network/status failures."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.get(url, **kwargs)
            if response.status_code in TRANSIENT_STATUS:
                last_error = httpx.HTTPStatusError(
                    f"Transient Wikimedia HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            else:
                response.raise_for_status()
                return response
        except (httpx.TimeoutException, httpx.TransportError) as error:
            last_error = error
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
    raise last_error or RuntimeError("Wikimedia request failed")


def _metadata_text(page, meta):
    categories = page.get("categories") or []
    category_text = " ".join(
        item.get("title", "") if isinstance(item, dict) else str(item)
        for item in categories
    )
    return " ".join((
        str(page.get("title", "") or ""),
        _clean(_value(meta.get("ImageDescription"))),
        _clean(_value(meta.get("ObjectName"))),
        category_text,
    ))


def _entity_evidence(title, metadata_text, topic_context):
    required = getattr(topic_context, "required_anchor_phrases", ()) or ()
    entity = required[0] if required else _normalized(getattr(topic_context, "primary_topic", "") or "")
    if not entity:
        return True, 0, 0
    title_normalized = _normalized(title)
    metadata_normalized = _normalized(metadata_text)
    entity_tokens = tuple(entity.split())
    title_tokens = set(title_normalized.split())
    metadata_tokens = set(metadata_normalized.split())
    phrase_in_title = f" {entity} " in f" {title_normalized} "
    phrase_in_metadata = f" {entity} " in f" {metadata_normalized} "
    all_title = bool(entity_tokens) and set(entity_tokens).issubset(title_tokens)
    all_metadata = bool(entity_tokens) and set(entity_tokens).issubset(metadata_tokens)
    matched = phrase_in_title or phrase_in_metadata or all_title or all_metadata
    score = 24 if phrase_in_title else 16 if all_title else 12 if phrase_in_metadata else 8 if all_metadata else 0
    return matched, score, len(set(entity_tokens) & title_tokens)


def _candidate_from_page(page, search_query, topic_context, current_sources, history_sources):
    info_list = page.get("imageinfo") or []
    if not info_list:
        return None, "MIME"
    info = info_list[0]
    mime = str(info.get("mime") or "").lower().strip()
    if mime not in ALLOWED_MIME:
        return None, "MIME"
    source = str(info.get("descriptionurl") or "").strip()
    if source and source in current_sources:
        return None, "duplicate-current-job"
    meta = info.get("extmetadata") or {}
    license_name = _clean(_value(meta.get("LicenseShortName")))
    if not _allowed_license(license_name):
        return None, "license"
    author = _clean(_value(meta.get("Artist")))
    if license_name.upper().startswith("CC BY") and not author:
        return None, "license"
    image_url = str(info.get("thumburl") or info.get("url") or "").strip()
    if not image_url:
        return None, "MIME"
    title = str(page.get("title", "") or "")
    searchable_title = _normalized(title)
    if any(_normalized(term) in searchable_title for term in _BANNED_TERMS):
        return None, "banned/document-type"
    metadata_text = _metadata_text(page, meta)
    relevance = score_media_candidate(
        topic_context, search_query, title=title, description=metadata_text,
        categories=page.get("categories") or (),
    )
    if not relevance.accepted:
        return None, "relevance"
    entity_match = relevance.entity_match
    score = relevance.score
    title_entity_matches = int(relevance.entity_match)
    context_title = relevance.context_matches
    context_metadata = 0
    return {
        "title": title, "source": source, "author": author,
        "license": license_name,
        "license_url": _clean(_value(meta.get("LicenseUrl"))),
        "image_url": image_url, "expected_mime": mime,
        "search_query_used": search_query, "relevance_score": score,
        "title_match_count": title_entity_matches + context_title,
        "entity_match": entity_match,
        "context_match_count": context_title + context_metadata,
        "history_duplicate": bool(source and source in history_sources),
    }, None


def _save_jpeg(content, output_path):
    with Image.open(BytesIO(content)) as image:
        image.load()
        if image.mode != "RGB":
            if "A" in image.mode:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
        image.thumbnail((1920, 1920))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, "JPEG", quality=92)


def _download_candidate(client, candidate, output_path):
    response = _request(client, candidate["image_url"])
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in ALLOWED_MIME:
        raise ValueError(f"unsupported actual MIME: {content_type or 'missing'}")
    declared_size = response.headers.get("content-length")
    if declared_size and int(declared_size) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds maximum size")
    if len(response.content) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds maximum size")
    _save_jpeg(response.content, Path(output_path))


def download_image(query, output_path, orientation=None, used_sources=None, offset=0,
                   topic_context=None, history_sources=None, client=None):
    """Search, rank and download an allowed Commons image with bounded effort."""
    del orientation
    query = str(query or "").strip()
    current_sources = set(used_sources or ())
    history_sources = set(history_sources or ()) - current_sources
    if not query:
        raise RuntimeError("Empty Wikimedia search query.")
    all_queries = _query_candidates(query, topic_context)
    own_client = client is None
    client = client or httpx.Client(timeout=25, follow_redirects=True, headers=HEADERS)
    rejection_counts = Counter()
    candidates_by_source = {}
    api_pages = 0
    total_seen = 0
    try:
        for search_query in all_queries:
            continuation = None
            query_eligible = 0
            for page_number in range(MAX_PAGES_PER_QUERY):
                if api_pages >= MAX_API_PAGES or total_seen >= MAX_CANDIDATES:
                    break
                params = {
                    "action": "query", "generator": "search", "gsrsearch": search_query,
                    "gsrnamespace": 6, "gsrlimit": PAGE_SIZE,
                    "prop": "imageinfo|categories", "cllimit": "max",
                    "iiprop": "url|mime|size|extmetadata", "iiurlwidth": 1920,
                    "format": "json", "formatversion": 2,
                }
                if continuation:
                    params.update(continuation)
                response = _request(client, API, params=params)
                payload = response.json()
                pages = payload.get("query", {}).get("pages", [])
                api_pages += 1
                total_seen += len(pages)
                for page in pages:
                    candidate, reason = _candidate_from_page(
                        page, search_query, topic_context, current_sources, history_sources
                    )
                    if reason:
                        rejection_counts[reason] += 1
                        continue
                    query_eligible += 1
                    key = candidate["source"] or candidate["image_url"]
                    previous = candidates_by_source.get(key)
                    if not previous or candidate["relevance_score"] > previous["relevance_score"]:
                        candidates_by_source[key] = candidate
                logger.debug(
                    "Wikimedia query=%r page=%d seen=%d eligible=%d rejections=%s",
                    search_query, page_number + 1, len(pages), query_eligible,
                    dict(rejection_counts),
                )
                continuation = payload.get("continue")
                fresh_count = sum(not item["history_duplicate"] for item in candidates_by_source.values())
                if not continuation or fresh_count >= MIN_FRESH_POOL:
                    break
            if sum(not item["history_duplicate"] for item in candidates_by_source.values()) >= MIN_FRESH_POOL:
                break
        ranked = sorted(
            candidates_by_source.values(),
            key=lambda item: (item["history_duplicate"], -item["relevance_score"], item["title"]),
        )
        if ranked and offset:
            fresh = [item for item in ranked if not item["history_duplicate"]]
            history = [item for item in ranked if item["history_duplicate"]]
            if fresh:
                shift = int(offset) % min(len(fresh), 4)
                fresh = fresh[shift:] + fresh[:shift]
            ranked = fresh + history
        fresh_ranked = [item for item in ranked if not item["history_duplicate"]]
        history_ranked = [item for item in ranked if item["history_duplicate"]]
        if fresh_ranked and history_ranked:
            download_pool = fresh_ranked[:MAX_DOWNLOAD_ATTEMPTS - 1] + history_ranked[:1]
        else:
            download_pool = ranked[:MAX_DOWNLOAD_ATTEMPTS]
        for candidate in download_pool:
            try:
                _download_candidate(client, candidate, output_path)
            except Exception as error:
                reason = "decode failure" if isinstance(error, (OSError, Image.DecompressionBombError)) else "download failure"
                rejection_counts[reason] += 1
                logger.debug("Wikimedia rejected title=%r reason=%s detail=%s", candidate["title"], reason, error)
                continue
            logger.debug(
                "Wikimedia selected query=%r title=%r score=%d history_duplicate=%s",
                candidate["search_query_used"], candidate["title"],
                candidate["relevance_score"], candidate["history_duplicate"],
            )
            return {
                "provider": "wikimedia", "media_type": "image",
                "media_file": str(Path(output_path)), "source": candidate["source"],
                "title": candidate["title"], "author": candidate["author"],
                "license": candidate["license"], "license_url": candidate["license_url"],
                "wikimedia_query": candidate["search_query_used"],
                "wikimedia_relevance_score": candidate["relevance_score"],
                "wikimedia_title_matches": candidate["title_match_count"],
                "wikimedia_entity_match": candidate["entity_match"],
                "wikimedia_context_matches": candidate["context_match_count"],
                "attribution": (
                    f"{candidate['title']} | Creator: {candidate['author']} | "
                    f"License: {candidate['license']} | {candidate['source']}"
                ),
            }
    finally:
        if own_client:
            client.close()
    logger.debug(
        "Wikimedia exhausted query=%r variants=%d pages=%d candidates=%d rejections=%s",
        query, len(all_queries), api_pages, len(candidates_by_source), dict(rejection_counts),
    )
    raise RuntimeError(
        f"No approved Wikimedia image found after bounded search; rejections={dict(rejection_counts)}"
    )
