import json
from pathlib import Path
import httpx

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {
    "User-Agent": "YouTubeAIGenerator/1.0 admin@attarasool.com"
}


def value(item):
    return item.get("value", "") if isinstance(item, dict) else ""


def download_image(query, output_path):
    params = {
        "action": "query", "generator": "search",
        "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 8,
        "prop": "imageinfo", "iiprop": "url|extmetadata",
        "iiurlwidth": 1920, "format": "json",
    }

    client = httpx.Client(
        timeout=40, follow_redirects=True, headers=HEADERS
    )
    data = client.get(API, params=params).json()
    pages = data.get("query", {}).get("pages", {})

    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(client.get(url).content)

        meta = info.get("extmetadata", {})
        return {
            "provider": "wikimedia",
            "source": info.get("descriptionurl", ""),
            "author": value(meta.get("Artist")),
            "license": value(meta.get("LicenseShortName")),
            "license_url": value(meta.get("LicenseUrl")),
        }

    raise RuntimeError(f"No Wikimedia image found: {query}")
