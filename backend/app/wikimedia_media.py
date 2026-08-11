from __future__ import annotations

import html
import re
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image


API = "https://commons.wikimedia.org/w/api.php"

HEADERS = {
    "User-Agent": (
        "AttaVideoAI/1.0 "
        "(https://attavideoai.site/; admin@attarasool.com)"
    )
}


def _value(item):
    if isinstance(item, dict):
        return str(item.get("value", "") or "")
    return str(item or "")


def _clean(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _allowed_license(name):
    name = (name or "").upper().strip()

    if "PUBLIC DOMAIN" in name:
        return True

    if "CC0" in name:
        return True

    if "CC BY" in name:
        blocked = (
            "BY-SA",
            "BY-NC",
            "BY-ND",
            "NONCOMMERCIAL",
            "NO DERIVATIVES",
        )
        return not any(x in name for x in blocked)

    return False


def _query_candidates(query):
    query = " ".join(str(query or "").split())

    candidates = [query]

    cleaned = query

    removable = (
        " historical portrait",
        " historical photograph",
        " historical photo",
        " archival photograph",
        " archival photo",
        " black and white photograph",
        " black and white photo",
        " historical archival",
        " historical",
        " portrait",
        " photograph",
        " photo",
        " archival",
    )

    lower = cleaned.lower()

    for phrase in removable:
        if phrase in lower:
            index = lower.find(phrase)
            cleaned = (
                cleaned[:index]
                + cleaned[index + len(phrase):]
            ).strip()
            lower = cleaned.lower()

    if cleaned and cleaned not in candidates:
        candidates.append(cleaned)

    descriptive_prefixes = {
        "young",
        "early",
        "old",
        "older",
        "late",
        "historic",
        "historical",
        "famous",
    }

    entity_words = cleaned.split()

    while (
        entity_words
        and entity_words[0].lower()
        in descriptive_prefixes
    ):
        entity_words.pop(0)

    entity_query = " ".join(entity_words)

    if (
        entity_query
        and entity_query not in candidates
    ):
        candidates.append(entity_query)

    # Do NOT arbitrarily truncate a query to its first
    # three or four words. That can turn meaningful subjects
    # such as "War of the Currents" into junk like "War of the".
    #
    # Safe candidates are now limited to:
    # 1. the original scene query
    # 2. the query with generic visual wording removed
    # 3. the entity query with descriptive prefixes removed
    #
    # If none of these produce a relevant Commons result,
    # Wikimedia should fail cleanly instead of broadening into
    # an unrelated historical image.

    return candidates


def _save_jpeg(content, output_path):
    with Image.open(BytesIO(content)) as image:
        image.load()

        if image.mode != "RGB":
            if "A" in image.mode:
                rgba = image.convert("RGBA")
                background = Image.new(
                    "RGB",
                    rgba.size,
                    (255, 255, 255),
                )
                background.paste(
                    rgba,
                    mask=rgba.getchannel("A"),
                )
                image = background
            else:
                image = image.convert("RGB")

        image.thumbnail((1920, 1920))

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        image.save(
            output_path,
            "JPEG",
            quality=92,
        )


def download_image(
    query,
    output_path,
    orientation=None,
    used_sources=None,
    offset=0,
):
    del orientation

    query = str(query or "").strip()
    used_sources = used_sources or set()

    if not query:
        raise RuntimeError("Empty Wikimedia search query.")

    with httpx.Client(
        timeout=40,
        follow_redirects=True,
        headers=HEADERS,
    ) as client:

        all_queries = _query_candidates(query)

        for search_query in all_queries:
            params = {
                "action": "query",
                "generator": "search",
                "gsrsearch": search_query,
                "gsrnamespace": 6,
                "gsrlimit": 20,
                "prop": "imageinfo",
                "iiprop": "url|mime|extmetadata",
                "iiurlwidth": 1920,
                "format": "json",
                "formatversion": 2,
            }

            response = client.get(API, params=params)
            response.raise_for_status()

            pages = (
                response.json()
                .get("query", {})
                .get("pages", [])
            )

            candidates = []

            for page in pages:
                info_list = page.get("imageinfo") or []

                if not info_list:
                    continue

                info = info_list[0]

                mime = str(
                    info.get("mime") or ""
                ).lower().strip()

                # Keep only normal raster images suitable for
                # automated video scenes. Reject PDFs, SVGs,
                # TIFF documents and other non-standard files.
                if mime not in {
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                }:
                    continue

                source = str(
                    info.get("descriptionurl") or ""
                ).strip()

                if source and source in used_sources:
                    continue

                meta = info.get("extmetadata") or {}

                license_name = _clean(
                    _value(meta.get("LicenseShortName"))
                )

                if not _allowed_license(license_name):
                    continue

                author = _clean(
                    _value(meta.get("Artist"))
                )

                if (
                    license_name.upper().startswith("CC BY")
                    and not author
                ):
                    continue

                image_url = (
                    info.get("thumburl")
                    or info.get("url")
                )

                if not image_url:
                    continue

                title = str(
                    page.get("title", "") or ""
                )

                description = _clean(
                    _value(meta.get("ImageDescription"))
                )

                title_searchable = title.lower()
                description_searchable = description.lower()

                banned_title_terms = {
                    " cover",
                    "catalogue",
                    "catalog of",
                    "title page",
                    "book cover",
                    "frontispiece",
                }

                if any(
                    term in title_searchable
                    for term in banned_title_terms
                ):
                    continue

                generic_words = {
                    "historical",
                    "historic",
                    "history",
                    "portrait",
                    "photograph",
                    "photo",
                    "archival",
                    "black",
                    "white",
                    "image",
                    "picture",
                    "young",
                    "early",
                    "old",
                    "older",
                    "late",
                    "famous",
                    "the",
                    "of",
                    "and",
                    "in",
                    "with",
                    "at",
                    "on",
                }

                query_tokens = [
                    word.lower().strip(
                        ".,:;!?()[]{}'\""
                    )
                    for word in search_query.split()
                ]

                important_tokens = [
                    token
                    for token in query_tokens
                    if len(token) >= 3
                    and token not in generic_words
                    and not token.isdigit()
                ]

                title_matches = [
                    token
                    for token in important_tokens
                    if token in title_searchable
                ]

                description_matches = [
                    token
                    for token in important_tokens
                    if token in description_searchable
                ]

                minimum_title_matches = (
                    2
                    if len(important_tokens) >= 3
                    else 1
                )

                if (
                    important_tokens
                    and len(title_matches)
                    < minimum_title_matches
                ):
                    continue

                # File-title matches are much stronger evidence
                # than incidental words inside a description.
                score = (
                    len(title_matches) * 5
                    + len(description_matches)
                )

                candidates.append({
                    "title": title,
                    "source": source,
                    "author": author,
                    "license": license_name,
                    "license_url": _clean(
                        _value(meta.get("LicenseUrl"))
                    ),
                    "image_url": image_url,
                    "search_query_used": search_query,
                    "relevance_score": score,
                    "title_match_count": len(title_matches),
                })

            if not candidates:
                continue

            candidates.sort(
                key=lambda item: item["relevance_score"],
                reverse=True,
            )

            top_score = candidates[0]["relevance_score"]

            best = [
                item
                for item in candidates
                if item["relevance_score"] == top_score
            ]

            candidate = best[
                int(offset or 0) % min(len(best), 4)
            ]

            media_response = client.get(
                candidate["image_url"]
            )
            media_response.raise_for_status()

            path = Path(output_path)

            _save_jpeg(
                media_response.content,
                path,
            )

            return {
                "provider": "wikimedia",
                "media_type": "image",
                "media_file": str(path),
                "source": candidate["source"],
                "title": candidate["title"],
                "author": candidate["author"],
                "license": candidate["license"],
                "license_url": candidate["license_url"],
                "wikimedia_query": candidate["search_query_used"],
                "wikimedia_relevance_score": candidate["relevance_score"],
                "wikimedia_title_matches": candidate.get(
                    "title_match_count",
                    0,
                ),
                "attribution": (
                    f"{candidate['title']} | "
                    f"Creator: {candidate['author']} | "
                    f"License: {candidate['license']} | "
                    f"{candidate['source']}"
                ),
            }

    raise RuntimeError(
        f"No approved Wikimedia image found after query fallbacks: {query}"
    )
