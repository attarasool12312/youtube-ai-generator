"""Safe user-facing download filenames; stored artifact names stay unchanged."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

_WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"}) | frozenset(
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
)
_MAX_STEM_LENGTH = 120


def safe_video_download_name(title: str | None, job_id: int | None = None) -> str:
    value = unicodedata.normalize("NFKC", str(title or ""))
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    value = re.sub(r'[\\/:*?"<>|]+', " ", value)
    value = re.sub(r"[^\w\-. ]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value).strip(" .-")
    value = re.sub(r"-{2,}", "-", value)
    if not value:
        value = "video"
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        value = f"video-{value}"
    value = value[:_MAX_STEM_LENGTH].rstrip(" .-") or "video"
    if job_id is not None and value.casefold() == "video":
        value = f"video-{int(job_id)}"
    return f"{value}.mp4"


def content_disposition_for_video(title: str | None, job_id: int | None = None) -> str:
    unicode_name = safe_video_download_name(title, job_id)
    ascii_stem = unicodedata.normalize("NFKD", unicode_name[:-4]).encode(
        "ascii", "ignore"
    ).decode("ascii")
    ascii_name = safe_video_download_name(ascii_stem, job_id)
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(unicode_name, safe='')}"
    )
