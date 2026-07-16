from pathlib import Path
import httpx

from app.api_settings import get_api_key
from app.openverse_client import openverse_headers

CLIENT = httpx.Client(timeout=60, follow_redirects=True)


def orientation_ok(width, height, orientation):
    if orientation == "portrait":
        return height > width
    if orientation == "square":
        return 0.8 <= width / max(height, 1) <= 1.25
    return width >= height


def save_media(url, output_path, headers=None):
    response = CLIENT.get(url, headers=headers)
    response.raise_for_status()

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return str(path)


def pexels_video(query, output_path, orientation):
    response = CLIENT.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": get_api_key("pexels_api_key")},
        params={"query": query, "per_page": 15},
    )
    response.raise_for_status()

    for item in response.json().get("videos", []):
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
                "source": item.get("url", ""),
                "author": item.get("user", {}).get("name", ""),
                "width": width,
                "height": height,
            }

    raise RuntimeError("No suitable Pexels video found.")


def pixabay_video(query, output_path, orientation):
    response = CLIENT.get(
        "https://pixabay.com/api/videos/",
        params={
            "key": get_api_key("pixabay_api_key"),
            "q": query,
            "per_page": 20,
            "safesearch": "true",
        },
    )
    response.raise_for_status()
    for item in response.json().get("hits", []):
        if item.get("isLowQuality") or item.get("isGRated"):
            continue

        for quality in ("large", "medium", "small"):
            file = item.get("videos", {}).get(quality) or {}
            width = file.get("width") or 0
            height = file.get("height") or 0

            if not file.get("url"):
                continue
            if not orientation_ok(width, height, orientation):
                continue
            save_media(file["url"], output_path)
            return {
                "provider": "pixabay",
                "media_type": "video",
                "source": item.get("pageURL", ""),
                "author": item.get("user", ""),
                "width": width,
                "height": height,
            }

    raise RuntimeError("No suitable Pixabay video found.")


def coverr_video(query, output_path, orientation):
    response = CLIENT.get(
        "https://api.coverr.co/videos",
        headers={"Authorization":
                 f"Bearer {get_api_key('coverr_api_key')}"},
        params={"query": query, "page_size": 15, "urls": "true"},
    )
    response.raise_for_status()
    for item in response.json().get("hits", []):
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
            "source": f"https://coverr.co/videos/{item.get('slug', '')}",
            "author": str(item.get("contributor_id") or ""),
            "width": width, "height": height,
        }

    raise RuntimeError("No suitable Coverr video found.")


def pexels_image(query, output_path, orientation):
    response = CLIENT.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": get_api_key("pexels_api_key")},
        params={"query": query, "per_page": 15},
    )
    response.raise_for_status()

    for item in response.json().get("photos", []):
        width, height = item["width"], item["height"]
        if not orientation_ok(width, height, orientation):
            continue
        save_media(item["src"]["large2x"], output_path)
        return {
            "provider": "pexels", "media_type": "image",
            "source": item["url"], "author": item["photographer"],
            "width": width, "height": height,
        }

    raise RuntimeError("No suitable Pexels image found.")


def pixabay_image(query, output_path, orientation):
    response = CLIENT.get(
        "https://pixabay.com/api/",
        params={"key": get_api_key("pixabay_api_key"),
                "q": query, "per_page": 20, "safesearch": "true"},
    )
    response.raise_for_status()

    for item in response.json().get("hits", []):
        if item.get("isLowQuality") or item.get("isGRated"):
            continue
        width, height = item["imageWidth"], item["imageHeight"]
        if not orientation_ok(width, height, orientation):
            continue
        save_media(item["largeImageURL"], output_path)
        return {
            "provider": "pixabay", "media_type": "image",
            "source": item["pageURL"], "author": item["user"],
            "width": width, "height": height,
        }

    raise RuntimeError("No suitable Pixabay image found.")


def openverse_image(query, output_path, orientation):
    response = CLIENT.get(
        "https://api.openverse.org/v1/images/",
        headers=openverse_headers(),
        params={"q": query, "page_size": 20, "mature": "false"},
    )
    response.raise_for_status()

    for item in response.json().get("results", []):
        width = item.get("width") or 0
        height = item.get("height") or 0
        if not item.get("url"):
            continue
        if not orientation_ok(width, height, orientation):
            continue

        save_media(item["url"], output_path)
        return {
            "provider": "openverse",
            "media_type": "image",
            "source": item.get("foreign_landing_url", ""),
            "author": item.get("creator", ""),
            "license": item.get("license", ""),
            "license_url": item.get("license_url", ""),
            "width": width,
            "height": height,
        }

    raise RuntimeError("No suitable Openverse image found.")
