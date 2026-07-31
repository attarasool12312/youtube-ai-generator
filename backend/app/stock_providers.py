from pathlib import Path
import re

import httpx

from app.api_settings import get_api_key
from app.openverse_client import openverse_headers

CLIENT = httpx.Client(timeout=60, follow_redirects=True)


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


def pixabay_query_candidates(query):
    normalized = normalized_query(query)
    words = normalized.split()

    candidates = [
        normalized,
        " ".join(words[:4]),
        " ".join(words[:2]),
    ]

    return [
        candidate
        for index, candidate in enumerate(candidates)
        if candidate and candidate not in candidates[:index]
    ]


def save_media(url, output_path, headers=None):
    response = CLIENT.get(url, headers=headers)
    response.raise_for_status()

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return str(path)


def pexels_video(query, output_path, orientation, used_sources=None, offset=0):
    response = CLIENT.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": get_api_key("pexels_api_key")},
        params={"query": query, "per_page": 15},
    )
    response.raise_for_status()

    items = rotate_items(
        response.json().get("videos", []), offset
    )
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
            save_media(file["link"], output_path)
            return {
                "provider": "pexels",
                "media_type": "video",
                "source": source,
                "author": item.get("user", {}).get("name", ""),
                "width": width,
                "height": height,
            }

    raise RuntimeError("No suitable Pexels video found.")


def pixabay_video(query, output_path, orientation, used_sources=None, offset=0):
    for candidate in pixabay_query_candidates(query):
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

        items = rank_pixabay_items(
            response.json().get("hits", []),
            candidate,
            offset,
        )

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

                save_media(url, output_path)
                return {
                    "provider": "pixabay",
                    "media_type": "video",
                    "source": source,
                    "author": item.get("user", ""),
                    "width": width,
                    "height": height,
                    "matched_query": candidate,
                }

    raise RuntimeError("No suitable Pixabay video found.")


def coverr_video(query, output_path, orientation, used_sources=None, offset=0):
    response = CLIENT.get(
        "https://api.coverr.co/videos",
        headers={"Authorization":
                 f"Bearer {get_api_key('coverr_api_key')}"},
        params={"query": query, "page_size": 15, "urls": "true"},
    )
    response.raise_for_status()
    items = rotate_items(
        response.json().get("hits", []), offset
    )
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

        save_media(url, output_path)
        return {
            "provider": "coverr", "media_type": "video",
            "source": source,
            "author": str(item.get("contributor_id") or ""),
            "width": width, "height": height,
        }

    raise RuntimeError("No suitable Coverr video found.")


def pexels_image(query, output_path, orientation, used_sources=None, offset=0):
    response = CLIENT.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": get_api_key("pexels_api_key")},
        params={"query": query, "per_page": 15},
    )
    response.raise_for_status()

    items = rotate_items(
        response.json().get("photos", []), offset
    )
    for item in items:
        source = item.get("url", "")
        if source in (used_sources or set()):
            continue
        width, height = item["width"], item["height"]
        if not orientation_ok(width, height, orientation):
            continue
        save_media(item["src"]["large2x"], output_path)
        return {
            "provider": "pexels", "media_type": "image",
            "source": source, "author": item["photographer"],
            "width": width, "height": height,
        }

    raise RuntimeError("No suitable Pexels image found.")


def pixabay_image(query, output_path, orientation, used_sources=None, offset=0):
    minimum_width, minimum_height = pixabay_minimum_dimensions(
        orientation
    )

    for candidate in pixabay_query_candidates(query):
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

        items = rank_pixabay_items(
            response.json().get("hits", []),
            candidate,
            offset,
        )

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

            save_media(url, output_path)
            return {
                "provider": "pixabay",
                "media_type": "image",
                "source": source,
                "author": item.get("user", ""),
                "width": width,
                "height": height,
                "matched_query": candidate,
            }

    raise RuntimeError("No suitable Pixabay image found.")


def openverse_image(query, output_path, orientation, used_sources=None, offset=0):
    response = CLIENT.get(
        "https://api.openverse.org/v1/images/",
        headers=openverse_headers(),
        params={"q": query, "page_size": 20, "mature": "false"},
    )
    response.raise_for_status()

    items = rotate_items(
        response.json().get("results", []), offset
    )
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

        save_media(item["url"], output_path)
        return {
            "provider": "openverse",
            "media_type": "image",
            "source": source,
            "author": item.get("creator", ""),
            "license": item.get("license", ""),
            "license_url": item.get("license_url", ""),
            "width": width,
            "height": height,
        }

    raise RuntimeError("No suitable Openverse image found.")
