from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import (
    Image,
    ImageDraw,
    ImageEnhance,
    ImageFont,
    ImageOps,
)

from app.ai_provider import call_ai
from app.local_image_generator import (
    generate_hd_image,
    get_runtime_profile,
)
from app.youtube_metadata_generator import (
    _extract_json_object,
    _script_excerpt,
    _validate_numeric_claims,
)


THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720
MAX_THUMBNAIL_BYTES = 1_900_000

FONT_CANDIDATES = (
    Path(
        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans-Bold.ttf"
    ),
    Path(
        "/usr/share/fonts/truetype/liberation2/"
        "LiberationSans-Bold.ttf"
    ),
)


@dataclass(frozen=True)
class GeneratedYouTubeThumbnail:
    path: str
    headline: str
    visual_prompt: str
    provider: str
    size_bytes: int


def _font_path() -> Path | None:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return candidate

    return None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path()

    if path is None:
        return ImageFont.load_default()

    return ImageFont.truetype(
        str(path),
        size=size,
    )


def _clean_headline(value: Any) -> str:
    headline = " ".join(
        str(value or "").replace("<", "").replace(">", "").split()
    ).strip(" -:|")

    if not headline:
        return "THE HIDDEN STORY"

    words = headline.split()

    if len(words) > 8:
        headline = " ".join(words[:8])

    return headline[:64].rstrip(" -:|")


def _fallback_headline(title: str) -> str:
    title = _clean_headline(title)
    words = title.split()

    if len(words) <= 7:
        return title

    return " ".join(words[:7])


def _generate_thumbnail_concept(
    *,
    title: str,
    script: str,
) -> tuple[str, str, str]:
    prompt = f"""
Create a high-click-through but honest YouTube thumbnail concept.

Return one valid JSON object only:
{{
  "headline": "...",
  "visual_prompt": "..."
}}

Rules:
- Base the concept only on the supplied title and script.
- Headline must be 3-7 words and no more than 44 characters.
- Headline must remain accurate and must not use misleading clickbait.
- Copy any number exactly from the source.
- Visual prompt must describe one clear cinematic focal subject.
- Put the focal subject mainly on the right side.
- Leave dark negative space on the left for headline text.
- Use high contrast, depth, dramatic lighting and realistic detail.
- Do not request text, captions, logos, UI, borders or watermarks.
- Do not mention YouTube in the image prompt.

Title:
{title}

Script:
{_script_excerpt(script)}
""".strip()

    try:
        result = call_ai(
            prompt,
            json_mode=True,
        )
        data = _extract_json_object(result.text)

        headline = _clean_headline(
            data.get("headline")
        )
        visual_prompt = " ".join(
            str(data.get("visual_prompt") or "").split()
        )

        _validate_numeric_claims(
            f"{title}\n{script}",
            headline,
        )

        if not visual_prompt:
            raise ValueError(
                "AI returned an empty thumbnail prompt."
            )

        visual_prompt = (
            visual_prompt
            + ", cinematic documentary photography, "
            "16:9 composition, high contrast, realistic, "
            "subject positioned on the right, dark negative "
            "space on the left, no text, no letters, no logos, "
            "no watermark"
        )

        return (
            headline,
            visual_prompt,
            result.provider,
        )

    except Exception:
        headline = _fallback_headline(title)
        visual_prompt = (
            f"Cinematic documentary visualization of {title}, "
            "one clear dramatic focal subject positioned on the "
            "right, realistic detail, deep contrast, atmospheric "
            "lighting, dark negative space on the left, "
            "16:9 composition, no text, no letters, no logos, "
            "no watermark"
        )

        return (
            headline,
            visual_prompt,
            "deterministic-fallback",
        )


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int = 3,
) -> list[str]:
    words = text.upper().split()
    lines: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join([*current, word])
        bbox = draw.textbbox(
            (0, 0),
            candidate,
            font=font,
        )

        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
            continue

        if current:
            lines.append(" ".join(current))

        current = [word]

    if current:
        lines.append(" ".join(current))

    if len(lines) <= max_lines:
        return lines

    retained = lines[:max_lines]
    remaining_words = " ".join(
        lines[max_lines - 1:]
    ).split()

    while remaining_words:
        last_line = " ".join(remaining_words) + "…"
        bbox = draw.textbbox(
            (0, 0),
            last_line,
            font=font,
        )

        if bbox[2] - bbox[0] <= max_width:
            retained[-1] = last_line
            break

        remaining_words.pop()

    return retained


def _fit_headline(
    draw: ImageDraw.ImageDraw,
    headline: str,
) -> tuple[ImageFont.ImageFont, list[str], int]:
    for size in range(86, 51, -2):
        font = _font(size)
        lines = _wrap_text(
            draw,
            headline,
            font,
            max_width=650,
            max_lines=3,
        )

        line_bbox = draw.textbbox(
            (0, 0),
            "Ag",
            font=font,
        )
        line_height = (
            line_bbox[3]
            - line_bbox[1]
            + 12
        )
        total_height = line_height * len(lines)

        if total_height <= 330:
            return font, lines, line_height

    font = _font(50)
    lines = _wrap_text(
        draw,
        headline,
        font,
        max_width=650,
        max_lines=3,
    )

    return font, lines, 66



VIDEO_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
}

THUMBNAIL_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "the",
    "their",
    "to",
    "your",
}


def _thumbnail_terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(
            r"[a-z0-9]+",
            str(value).casefold(),
        )
        if len(term) > 2
        and term not in THUMBNAIL_STOP_WORDS
    }


def _select_fast_thumbnail_source(
    package: dict[str, Any],
    title: str,
) -> Path | None:
    title_terms = _thumbnail_terms(title)
    best_path: Path | None = None
    best_score = -1

    for scene in package.get("scenes", []):
        media_value = str(
            scene.get("media_file") or ""
        ).strip()

        if not media_value:
            continue

        media_path = Path(
            media_value
        ).expanduser().resolve()

        if not media_path.is_file():
            continue

        scene_text = " ".join([
            str(scene.get("query") or ""),
            str(scene.get("narration") or ""),
            str(scene.get("image_prompt") or ""),
            str(scene.get("stock_keywords") or ""),
        ])

        overlap = len(
            title_terms
            & _thumbnail_terms(scene_text)
        )

        score = overlap * 10

        if media_path.suffix.lower() not in VIDEO_SUFFIXES:
            score += 3

        scene_number = int(
            scene.get("scene_number") or 0
        )

        if 1 <= scene_number <= 8:
            score += 1

        if score > best_score:
            best_score = score
            best_path = media_path

    return best_path


def _create_fast_thumbnail_background(
    package: dict[str, Any],
    title: str,
    output_path: Path,
) -> str | None:
    source_path = _select_fast_thumbnail_source(
        package,
        title,
    )

    if source_path is None:
        return None

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if source_path.suffix.lower() in VIDEO_SUFFIXES:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                "1.0",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-vf",
                (
                    "scale=1280:720:"
                    "force_original_aspect_ratio=increase,"
                    "crop=1280:720"
                ),
                str(output_path),
            ],
            check=True,
            timeout=120,
        )
    else:
        with Image.open(source_path) as image:
            fitted = ImageOps.fit(
                image.convert("RGB"),
                (
                    THUMBNAIL_WIDTH,
                    THUMBNAIL_HEIGHT,
                ),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )

            fitted.save(
                output_path,
                format="PNG",
                optimize=True,
            )

    return str(source_path)


def _apply_overlays(
    image: Image.Image,
    headline: str,
) -> Image.Image:
    image = image.convert("RGB").resize(
        (
            THUMBNAIL_WIDTH,
            THUMBNAIL_HEIGHT,
        ),
        Image.Resampling.LANCZOS,
    )

    image = ImageEnhance.Contrast(image).enhance(1.12)
    image = ImageEnhance.Color(image).enhance(1.08)
    image = image.convert("RGBA")

    overlay = Image.new(
        "RGBA",
        image.size,
        (0, 0, 0, 0),
    )
    overlay_draw = ImageDraw.Draw(overlay)

    for x in range(900):
        ratio = x / 900
        alpha = int(235 * (1 - ratio) ** 1.7)
        overlay_draw.line(
            (x, 0, x, THUMBNAIL_HEIGHT),
            fill=(4, 8, 16, alpha),
        )

    for y in range(THUMBNAIL_HEIGHT):
        edge_ratio = min(
            y,
            THUMBNAIL_HEIGHT - y,
        ) / (THUMBNAIL_HEIGHT / 2)
        alpha = int(
            70 * (1 - min(1, edge_ratio))
        )
        overlay_draw.line(
            (0, y, THUMBNAIL_WIDTH, y),
            fill=(0, 0, 0, alpha),
        )

    image = Image.alpha_composite(
        image,
        overlay,
    )
    draw = ImageDraw.Draw(image)

    label_font = _font(25)
    label = "EXPLAINED"
    label_bbox = draw.textbbox(
        (0, 0),
        label,
        font=label_font,
    )
    label_width = label_bbox[2] - label_bbox[0]

    draw.rounded_rectangle(
        (
            70,
            108,
            70 + label_width + 34,
            154,
        ),
        radius=10,
        fill=(255, 194, 64, 235),
    )
    draw.text(
        (87, 116),
        label,
        font=label_font,
        fill=(10, 14, 24, 255),
    )

    headline_font, lines, line_height = (
        _fit_headline(
            draw,
            headline,
        )
    )

    y = 183

    for line in lines:
        draw.text(
            (70 + 4, y + 5),
            line,
            font=headline_font,
            fill=(0, 0, 0, 185),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 185),
        )
        draw.text(
            (70, y),
            line,
            font=headline_font,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 220),
        )
        y += line_height

    draw.rounded_rectangle(
        (
            70,
            615,
            280,
            624,
        ),
        radius=4,
        fill=(255, 194, 64, 255),
    )

    return image.convert("RGB")


def _save_optimized_jpeg(
    image: Image.Image,
    output_path: Path,
) -> int:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    for quality in (
        92,
        88,
        84,
        80,
        76,
        72,
        68,
    ):
        image.save(
            output_path,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling=1,
        )

        size_bytes = output_path.stat().st_size

        if size_bytes <= MAX_THUMBNAIL_BYTES:
            return size_bytes

    raise RuntimeError(
        "Generated thumbnail could not be reduced "
        "below the safe file-size limit."
    )


def generate_youtube_thumbnail(
    video_job: Any,
    *,
    publish_title: str | None = None,
) -> GeneratedYouTubeThumbnail:
    output_directory = Path(
        video_job.output_directory
    )
    package_path = (
        output_directory
        / "package.json"
    )
    final_path = (
        output_directory
        / "youtube-thumbnail.jpg"
    )
    background_path = (
        output_directory
        / "youtube-thumbnail-background.png"
    )
    details_path = (
        output_directory
        / "youtube-thumbnail.json"
    )

    if (
        final_path.is_file()
        and final_path.stat().st_size > 10_000
        and details_path.is_file()
    ):
        details = json.loads(
            details_path.read_text(
                encoding="utf-8"
            )
        )

        return GeneratedYouTubeThumbnail(
            path=str(final_path),
            headline=str(
                details.get("headline") or ""
            ),
            visual_prompt=str(
                details.get("visual_prompt") or ""
            ),
            provider=str(
                details.get("provider") or "existing"
            ),
            size_bytes=final_path.stat().st_size,
        )

    if not package_path.is_file():
        raise FileNotFoundError(
            f"Video package not found: {package_path}"
        )

    package = json.loads(
        package_path.read_text(
            encoding="utf-8"
        )
    )
    title = str(
        publish_title
        or package.get("title")
        or video_job.title
    ).strip()
    script = str(
        package.get("script") or ""
    ).strip()

    headline, visual_prompt, provider = (
        _generate_thumbnail_concept(
            title=title,
            script=script,
        )
    )

    seed_identity = (
        f"{video_job.id}|{title}|{visual_prompt}"
    )
    seed = int(
        sha256(
            seed_identity.encode("utf-8")
        ).hexdigest()[:8],
        16,
    )

    runtime = get_runtime_profile(
        "landscape"
    )
    background_provider = "local-flux"

    if runtime["profile"] == "low":
        selected_source = (
            _create_fast_thumbnail_background(
                package,
                title,
                background_path,
            )
        )

        if selected_source:
            background_provider = (
                "existing-media-fast-mode"
            )
        else:
            generate_hd_image(
                prompt=visual_prompt,
                output_path=background_path,
                orientation="landscape",
                seed=seed,
            )
            background_provider = (
                "local-flux-low-fallback"
            )
    else:
        generate_hd_image(
            prompt=visual_prompt,
            output_path=background_path,
            orientation="landscape",
            seed=seed,
        )
        background_provider = (
            "local-flux-high-quality"
        )

    final_provider = (
        f"{provider}+{background_provider}"
    )

    background = Image.open(
        background_path
    )
    final_image = _apply_overlays(
        background,
        headline,
    )
    size_bytes = _save_optimized_jpeg(
        final_image,
        final_path,
    )

    details = {
        "headline": headline,
        "visual_prompt": visual_prompt,
        "provider": final_provider,
        "runtime_profile": runtime["profile"],
        "background_provider": background_provider,
        "seed": seed,
        "width": THUMBNAIL_WIDTH,
        "height": THUMBNAIL_HEIGHT,
        "size_bytes": size_bytes,
        "path": str(final_path),
    }
    details_path.write_text(
        json.dumps(
            details,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return GeneratedYouTubeThumbnail(
        path=str(final_path),
        headline=headline,
        visual_prompt=visual_prompt,
        provider=final_provider,
        size_bytes=size_bytes,
    )
