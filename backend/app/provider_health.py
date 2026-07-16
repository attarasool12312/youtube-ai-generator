import httpx

from app.api_settings import get_api_key
from app.openverse_client import openverse_headers

QUERY = "modern technology office"


def result(name, response, field):
    response.raise_for_status()
    count = len(response.json().get(field, []))
    print(f"{name}: OK — {count} result(s)")


def check_pexels():
    response = httpx.get(
        "https://api.pexels.com/v1/videos/search",
        headers={"Authorization": get_api_key("pexels_api_key")},
        params={"query": QUERY, "per_page": 2},
        timeout=30,
    )
    result("Pexels Video", response, "videos")


def check_pixabay():
    response = httpx.get(
        "https://pixabay.com/api/videos/",
        params={"key": get_api_key("pixabay_api_key"),
                "q": QUERY, "per_page": 3},
        timeout=30,
    )
    result("Pixabay Video", response, "hits")


def check_coverr():
    response = httpx.get(
        "https://api.coverr.co/videos",
        headers={"Authorization":
                 f"Bearer {get_api_key('coverr_api_key')}"},
        params={"query": QUERY, "page_size": 2, "urls": "true"},
        timeout=30,
    )
    result("Coverr Video", response, "hits")


def check_openverse():
    response = httpx.get(
        "https://api.openverse.org/v1/images/",
        headers=openverse_headers(),
        params={"q": QUERY, "page_size": 2},
        timeout=30,
    )
    result("Openverse Image", response, "results")


for name, check in [
    ("Pexels", check_pexels),
    ("Pixabay", check_pixabay),
    ("Coverr", check_coverr),
    ("Openverse", check_openverse),
]:
    try:
        check()
    except httpx.HTTPStatusError as error:
        print(f"{name}: FAILED — HTTP {error.response.status_code}")
    except Exception as error:
        print(f"{name}: FAILED — {type(error).__name__}")
