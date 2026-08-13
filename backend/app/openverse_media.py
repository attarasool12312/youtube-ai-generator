"""Hardened Openverse adapter for licensed historical imagery."""
from __future__ import annotations

from collections import Counter
import logging
from pathlib import Path

from app.media_relevance import build_media_query_candidates, media_text_tokens, normalize_media_text, score_media_candidate

from app.media_relevance import build_media_query_candidates, media_text_tokens, normalize_media_text
from app.openverse_client import openverse_headers
from app.wikimedia_media import (
    MAX_DOWNLOAD_ATTEMPTS, _download_candidate, _entity_evidence,
    _request,
)

API = "https://api.openverse.org/v1/images/"
PAGE_SIZE = 20
MAX_PAGES_PER_QUERY = 2
MAX_API_PAGES = 6
MAX_RESULTS = 120
MIN_FRESH_POOL = 5
_ALLOWED_LICENSES = frozenset({"pdm", "cc0", "by"})
_ALLOWED_FILETYPES = frozenset({"jpg", "jpeg", "png", "webp"})
logger = logging.getLogger(__name__)
_PERSON_NAMED_PLACE_PHRASES = (
    "airport", "hospital", "nursing home", "road", "school", "station", "street",
)
_PERSON_DEPICTION_TERMS = frozenset({
    "award", "bust", "ceremony", "engraving", "laboratory", "members",
    "painting", "photo", "photograph", "portrait",
    "saluting", "scientist", "sculpture", "seated", "speaking", "standing",
    "statue", "with",
})


def _license_details(item):
    slug = normalize_media_text(item.get("license")).replace(" ", "")
    if slug not in _ALLOWED_LICENSES:
        return None
    creator = " ".join(str(item.get("creator") or "").split())
    if slug == "by" and not creator:
        return None
    display = {"pdm": "Public Domain Mark", "cc0": "CC0", "by": "CC BY"}[slug]
    version = " ".join(str(item.get("license_version") or "").split())
    if version and slug == "by":
        display = f"{display} {version}"
    return slug, display, creator


def _tag_text(item):
    values = []
    for tag in item.get("tags") or []:
        values.append(str(tag.get("name") or "") if isinstance(tag, dict) else str(tag))
    return " ".join(values)


def _candidate(item, search_query, topic_context, current_sources, history_sources):
    license_details = _license_details(item)
    if not license_details:
        return None, "license"
    license_slug, license_name, creator = license_details
    source = str(item.get("foreign_landing_url") or item.get("detail_url") or "").strip()
    if not source:
        return None, "source"
    if source in current_sources:
        return None, "duplicate-current-job"
    image_url = str(item.get("url") or "").strip()
    if not image_url:
        return None, "MIME"
    filetype = normalize_media_text(item.get("filetype")).replace(" ", "")
    if filetype and filetype not in _ALLOWED_FILETYPES:
        return None, "MIME"
    title = " ".join(str(item.get("title") or "").split())
    description = " ".join(str(item.get("description") or "").split())
    support = " ".join((title, description, _tag_text(item)))
    relevance = score_media_candidate(
        topic_context, search_query, title=title, description=description,
        tags=item.get("tags") or (),
    )
    if not relevance.accepted:
        return None, "relevance"
    entity_match = relevance.entity_match
    score = relevance.score
    title_entity_matches = int(relevance.entity_match)
    context_title = relevance.context_matches
    context_support = 0
    return {
        "title": title or "Untitled Openverse image",
        "source": source,
        "author": creator,
        "license": license_name,
        "license_slug": license_slug,
        "license_url": str(item.get("license_url") or "").strip(),
        "image_url": image_url,
        "search_query_used": search_query,
        "relevance_score": score,
        "title_match_count": title_entity_matches + context_title,
        "entity_match": entity_match,
        "context_match_count": context_title + context_support,
        "history_duplicate": source in history_sources,
    }, None


def download_historical_image(query, output_path, orientation=None, used_sources=None,
                              offset=0, topic_context=None, history_sources=None,
                              client=None, headers=None):
    """Search and download a conservative-license Openverse historical image."""
    del orientation
    query = " ".join(str(query or "").split()).strip()
    if not query:
        raise RuntimeError("Empty Openverse search query.")
    current_sources = set(used_sources or ())
    history_sources = set(history_sources or ()) - current_sources
    variants = build_media_query_candidates(query, topic_context)
    own_client = client is None
    client = client or httpx.Client(timeout=25, follow_redirects=True)
    if headers is not None:
        request_headers = headers
    else:
        try:
            request_headers = openverse_headers()
        except Exception as error:
            logger.debug("Openverse authentication unavailable; using anonymous API: %s", error)
            request_headers = {}
    rejection_counts = Counter()
    candidates_by_source = {}
    api_pages = 0
    total_seen = 0
    try:
        for search_query in variants:
            for page_number in range(1, MAX_PAGES_PER_QUERY + 1):
                if api_pages >= MAX_API_PAGES or total_seen >= MAX_RESULTS:
                    break
                response = _request(client, API, headers=request_headers, params={
                    "q": search_query, "page": page_number,
                    "page_size": PAGE_SIZE, "mature": "false",
                })
                payload = response.json()
                results = payload.get("results") or []
                api_pages += 1
                total_seen += len(results)
                eligible = 0
                for item in results:
                    candidate, reason = _candidate(
                        item, search_query, topic_context, current_sources, history_sources
                    )
                    if reason:
                        rejection_counts[reason] += 1
                        continue
                    eligible += 1
                    previous = candidates_by_source.get(candidate["source"])
                    if not previous or candidate["relevance_score"] > previous["relevance_score"]:
                        candidates_by_source[candidate["source"]] = candidate
                logger.debug(
                    "Openverse query=%r page=%d results=%d eligible=%d rejections=%s",
                    search_query, page_number, len(results), eligible, dict(rejection_counts),
                )
                fresh_count = sum(not item["history_duplicate"] for item in candidates_by_source.values())
                page_count = int(payload.get("page_count") or page_number)
                if not results or page_number >= page_count or fresh_count >= MIN_FRESH_POOL:
                    break
            if sum(not item["history_duplicate"] for item in candidates_by_source.values()) >= MIN_FRESH_POOL:
                break
        ranked = sorted(
            candidates_by_source.values(),
            key=lambda item: (item["history_duplicate"], -item["relevance_score"], item["title"]),
        )
        if offset:
            fresh = [item for item in ranked if not item["history_duplicate"]]
            history = [item for item in ranked if item["history_duplicate"]]
            if fresh:
                shift = int(offset) % min(len(fresh), 4)
                fresh = fresh[shift:] + fresh[:shift]
            ranked = fresh + history
        fresh = [item for item in ranked if not item["history_duplicate"]]
        history = [item for item in ranked if item["history_duplicate"]]
        pool = fresh[:MAX_DOWNLOAD_ATTEMPTS - 1] + history[:1] if fresh and history else ranked[:MAX_DOWNLOAD_ATTEMPTS]
        for candidate in pool:
            try:
                _download_candidate(client, candidate, output_path)
            except Exception as error:
                rejection_counts["download/decode failure"] += 1
                logger.debug("Openverse rejected title=%r reason=download/decode detail=%s", candidate["title"], error)
                continue
            logger.debug(
                "Openverse selected query=%r title=%r score=%d history_duplicate=%s",
                candidate["search_query_used"], candidate["title"],
                candidate["relevance_score"], candidate["history_duplicate"],
            )
            return {
                "provider": "openverse", "media_type": "image",
                "media_file": str(Path(output_path)), "source": candidate["source"],
                "title": candidate["title"], "author": candidate["author"],
                "license": candidate["license"], "license_url": candidate["license_url"],
                "matched_query": candidate["search_query_used"],
                "openverse_query": candidate["search_query_used"],
                "openverse_relevance_score": candidate["relevance_score"],
                "openverse_entity_match": candidate["entity_match"],
                "openverse_context_matches": candidate["context_match_count"],
                "attribution": (
                    f"{candidate['title']} | Creator: {candidate['author']} | "
                    f"License: {candidate['license']} | {candidate['license_url']} | "
                    f"{candidate['source']}"
                ),
            }
    finally:
        if own_client:
            client.close()
    logger.debug(
        "Openverse exhausted query=%r variants=%d pages=%d candidates=%d rejections=%s",
        query, len(variants), api_pages, len(candidates_by_source), dict(rejection_counts),
    )
    raise RuntimeError(
        f"No approved Openverse image found after bounded search; rejections={dict(rejection_counts)}"
    )
