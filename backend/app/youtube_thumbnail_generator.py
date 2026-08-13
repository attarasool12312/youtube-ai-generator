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

from app.ai_provider import call_gemini
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
TYPOGRAPHY_PRESETS = (
    ("white_dimension", (255, 255, 255, 255), (8, 12, 22, 255), (30, 38, 54, 245)),
    ("gold_impact", (255, 211, 62, 255), (20, 12, 2, 255), (105, 55, 5, 245)),
    ("cool_premium", (220, 240, 255, 255), (3, 12, 29, 255), (10, 45, 82, 245)),
)

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


@dataclass(frozen=True)
class GeneratedYouTubeThumbnailPrompt:
    headline: str
    visual_prompt: str
    provider: str


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

    words = headline.split()

    if len(words) > 8:
        headline = " ".join(words[:8])

    return headline[:64].rstrip(" -:|")


def _fallback_headline(title: str) -> str:
    cleaned = _clean_headline(title)
    if not cleaned:
        return "UNTITLED VIDEO"
    words = cleaned.split()
    # Keep the concrete beginning of the actual title; strip only structural
    # question wrappers when enough topic wording remains.
    if len(words) > 2 and words[0].casefold() in {"how", "why", "what", "who"}:
        words.pop(0)
        while len(words) > 2 and words[0].casefold() in {
            "a", "an", "do", "does", "did", "if", "is", "are", "the", "was", "were"
        }:
            words.pop(0)
    return _clean_headline(" ".join(words[:7])) or cleaned


def _external_thumbnail_prompt(title: str, headline: str, visual_prompt: str) -> str:
    lowered = visual_prompt.casefold()
    contradictions = (
        "do not render text", "do not render any text", "render no text",
        "text-free", "without text",
    )
    if any(phrase in lowered for phrase in contradictions):
        raise ValueError("External thumbnail prompt contradicts rendered-headline contract.")
    return (
        f"Create a complete premium 16:9 YouTube thumbnail for {title}. "
        f"Use this exact headline as visible rendered text: \"{headline}\". "
        "Give the headline deliberate placement, bold professional typography, "
        "strong contrast, and clear mobile-size readability. Do not change or add "
        f"words. Visual composition: {visual_prompt}"
    )


def _generate_thumbnail_concept(
    *,
    title: str,
    script: str,
) -> tuple[str, str, str]:
    prompt = f"""
Act as an elite YouTube thumbnail creative director.

Create ONE premium, high-click-through but honest thumbnail concept
specifically for the supplied video title and final script.

The result must feel custom-designed for THIS exact topic.
Never fall back to a generic stock-photo template.

Return one valid JSON object only:
{{
    "headline": "...",
    "visual_prompt": "..."
}}

HEADLINE:
- Create a short, powerful thumbnail headline.
- Prefer 2-7 words and maximum 44 characters.
- Make it instantly understandable on a small mobile screen.
- It may be different from the video title if a shorter phrase
  communicates the same truthful idea more strongly.
- Never use misleading clickbait.
- Preserve important numbers, years, locations and factual claims.
- Copy important numbers exactly from the supplied source.

CREATIVE DIRECTION:
- Analyze the title and script before choosing the visual.
- Identify the video's strongest visual hook: its main promise,
  question, tension, transformation, discovery, result, danger,
  scale, contrast, emotion or surprising idea.
- Build the thumbnail around that hook.
- The image must communicate the topic within about one second.
- Prefer a memorable topic-specific visual idea over a generic scene.
- Use visual storytelling rather than unnecessary decoration.

SUBJECT SELECTION:
- Do NOT automatically use a person, laptop, office, hologram,
  glowing nodes, money, arrows, charts or generic technology imagery.
- Use those only when they genuinely strengthen this exact topic.
- A human subject is optional.
- Depending on the topic, the focal point may instead be an object,
  environment, machine, landmark, historical scene, scientific
  phenomenon, product, creature, map-like visual, transformation,
  comparison, symbolic concept or dramatic event.
- Keep one dominant visual idea and only meaningful supporting elements.
- Avoid clutter.

LAYOUT:
- Choose the strongest composition for THIS specific concept.
- Vary the layout naturally from video to video.
- Do not repeatedly force the subject onto the same side.
- Possible compositions include:
  subject left / text right,
  subject right / text left,
  centered hero composition,
  close-up object composition,
  split comparison,
  before-and-after,
  foreground/background scale contrast,
  symmetrical layout,
  editorial collage,
  dramatic wide scene,
  or another layout that better serves the topic.
- Reserve a deliberate, clean text-safe area for the headline.
- The text-safe area can be left, right, top, bottom or intelligently
  integrated into the composition.
- Main subjects must remain large and recognizable at thumbnail size.

VISUAL QUALITY:
- Aim for premium professional YouTube thumbnail art direction,
  not a generic cinematic stock photograph.
- Use strong visual hierarchy and clear subject separation.
- Choose the color palette, lighting, atmosphere and visual treatment
  according to the topic.
- Do not default to dark blue/orange cinematic lighting.
- Use bold contrast and controlled color relationships.
- Create depth where appropriate.
- Make the composition polished, deliberate and visually expensive.
- Photorealistic, documentary, commercial, editorial, illustrated,
  conceptual, product-style or another treatment may be chosen when
  it best fits the topic.
- Avoid repetitive AI-looking effects and meaningless glowing details.

VISUAL PROMPT OUTPUT:
- Write a detailed image-generation prompt for the complete composition.
- Clearly describe:
  main focal subject,
  meaningful supporting elements,
  setting/environment,
  framing or camera perspective,
  subject placement,
  text-safe area,
  lighting,
  color direction,
  depth,
  emotion/mood,
  and visual style.
- The prompt should be specific enough to produce a designed thumbnail,
  not merely a nice-looking background image.
- Do NOT ask the image model to render text in visual_prompt because
  the application adds the exact generated headline afterward.
- Do not request extra captions, logos, watermarks, UI or borders.
- Do not mention these instructions or JSON in visual_prompt.

Title:
{title}

Final script:
{_script_excerpt(script)}
""".strip()

    result = call_gemini(
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

    if not headline:
        raise ValueError(
            "Gemini returned an empty thumbnail headline."
        )

    if not visual_prompt:
        raise ValueError(
            "Gemini returned an empty thumbnail visual prompt."
        )

    return (
        headline,
        visual_prompt,
        result.provider,
    )


def _local_thumbnail_concept(title: str, script: str) -> tuple[str, str, str]:
    """Return a text-free background contract with deterministic fallback."""
    try:
        return _generate_thumbnail_concept(title=title, script=script)
    except Exception as error:
        headline = _fallback_headline(title)
        visual_prompt = (
            f"Create a premium professional text-free 16:9 YouTube thumbnail "
            f"background for this exact topic: {title}. Show one large, recognizable "
            "primary subject and only meaningful supporting elements, with strong "
            "visual hierarchy, subject separation, deliberate lighting, and a clean "
            "high-contrast area where the application will overlay its headline. "
            "Do not render text, letters, captions, logos, or watermarks. Avoid "
            "generic holograms and meaningless glowing effects."
        )
        return headline, visual_prompt, f"deterministic-local-fallback:{type(error).__name__}"


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


def _fit_headline(draw, headline):
    for size in range(92, 35, -2):
        font = _font(size)
        lines = _wrap_text(draw, headline, font, max_width=680, max_lines=20)
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        line_height = bbox[3] - bbox[1] + 5
        if len(lines) <= 4 and line_height * len(lines) <= 350:
            return font, lines, line_height
    font = _font(34)
    lines = _wrap_text(draw, headline, font, max_width=680, max_lines=20)
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return font, lines, bbox[3] - bbox[1] + 4


def _typography_preset(headline):
    index = int(sha256(headline.encode("utf-8")).hexdigest()[:8], 16)
    return TYPOGRAPHY_PRESETS[index % len(TYPOGRAPHY_PRESETS)]


def _draw_premium_headline(image, headline, x=70, top=183):
    draw = ImageDraw.Draw(image)
    font, lines, line_height = _fit_headline(draw, headline)
    name, face, stroke, depth = _typography_preset(headline)
    for index, line in enumerate(lines):
        y = top + index * line_height
        draw.text((x + 14, y + 16), line, font=font, fill=(0, 0, 0, 145), stroke_width=8, stroke_fill=(0, 0, 0, 130))
        for offset in range(9, 0, -1):
            draw.text((x + offset, y + offset), line, font=font, fill=depth, stroke_width=7, stroke_fill=stroke)
        draw.text((x, y), line, font=font, fill=face, stroke_width=7, stroke_fill=stroke)
        draw.text((x + 2, y + 1), line, font=font, fill=face, stroke_width=1, stroke_fill=(255, 255, 255, 150))
    width = int(max((draw.textlength(line, font=font) for line in lines), default=0))
    return lines, (x, top, x + width + 24, top + len(lines) * line_height + 25), name



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

    _draw_premium_headline(image, headline)

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


def _generate_youtube_thumbnail_prompt_ai(
    video_job: Any,
    *,
    publish_title: str | None = None,
) -> GeneratedYouTubeThumbnailPrompt:
    output_directory = Path(video_job.output_directory or "")
    package_path = output_directory / "package.json"

    if not package_path.is_file():
        raise FileNotFoundError(
            f"Video package not found: {package_path}"
        )

    package = json.loads(
        package_path.read_text(encoding="utf-8")
    )

    title = str(
        publish_title
        or package.get("title")
        or video_job.title
    ).strip()

    script = str(
        package.get("script")
        or video_job.script
        or ""
    ).strip()

    if not title:
        raise ValueError("Video title is empty.")

    if not script:
        raise ValueError("Final video script is empty.")

    summary_prompt = f"""
Read this video title and final script excerpt.

Write one concise thumbnail-design summary explaining:
- what the video is really about
- the main promise, question, result or story
- the strongest concrete visual subjects
- important names, places, numbers, years or objects
- the emotional angle viewers should instantly understand

Do not create a thumbnail or headline yet.
Do not invent information.
Return one concise paragraph only.

VIDEO TITLE:
{title}

FINAL SCRIPT:
{_script_excerpt(script)}
""".strip()

    summary_result = call_gemini(
        summary_prompt,
        json_mode=False,
    )

    script_summary = " ".join(
        summary_result.text.split()
    ).strip()

    if not script_summary:
        raise ValueError(
            "Gemini returned an empty script summary."
        )

    headline_prompt = f"""
Act as an elite YouTube thumbnail headline strategist.

Create ONE short, powerful, high-click-through thumbnail phrase for
the video below.

The phrase must immediately tell viewers what the video is about or
what valuable result, question, tension, discovery or transformation
they should expect.

This system is used for EVERY niche, so adapt naturally to the topic.

STRICT RULES:
- Prefer clarity and impact over cleverness.
- The phrase must be strongly anchored to the actual video topic.
- Prefer 2-6 words.
- Maximum 38 characters when practical.
- Use a concrete topic keyword, outcome, subject, name, place, number
  or recognizable idea from the title/summary whenever appropriate.
- A viewer who sees ONLY the thumbnail phrase should still understand
  the general topic.
- Complement the title instead of becoming unrelated to it.
- Preserve important years, numbers, names and factual claims.
- Benefit-driven wording is excellent when the video promises a result.
- Curiosity is allowed only when the topic remains clear.

NEVER return vague filler such as:
THE METHOD
THE SECRET
THE TRUTH
SOLO IN 2026
THIS CHANGES EVERYTHING
YOU NEED THIS
WATCH THIS
GAME CHANGER

Do not invent claims unsupported by the title or summary.

Return JSON only:
{{
    "headline": "..."
}}

VIDEO TITLE:
{title}

VIDEO SUMMARY:
{script_summary}
""".strip()

    headline_result = call_gemini(
        headline_prompt,
        json_mode=True,
    )

    headline_data = _extract_json_object(
        headline_result.text
    )

    headline = _clean_headline(
        headline_data.get("headline")
    )

    if not headline:
        raise ValueError(
            "Gemini returned an empty thumbnail headline."
        )

    creative_prompt = f"""
Act as a world-class YouTube thumbnail designer and AI image-prompt
specialist.

Create ONE complete image-generation prompt for a premium,
high-performing YouTube thumbnail.

This is NOT a movie poster.
This is NOT a corporate stock photograph.
This is NOT a minimalist magazine advertisement.
This is NOT automatically a dark cinematic scene.

Design something that looks intentionally made for a successful
YouTube thumbnail.

Adapt completely to the actual niche and topic.

Choose whatever combination of people, objects, environments,
expressions, transformations, comparisons, scale, symbols, colors,
lighting and composition best communicates THIS exact video.

UNIVERSAL QUALITY RULES:
- Make the main idea understandable within one second.
- Use a strong, unmistakable focal point.
- Make important subjects large and visually dominant.
- Use bold visual hierarchy.
- Use energetic, compelling visual storytelling when appropriate.
- Colors should feel intentional, attractive and attention-grabbing.
- Maintain excellent readability at small mobile size.
- Avoid unnecessary empty space.
- Avoid generic stock-photo poses.
- Avoid generic laptop/office imagery unless genuinely useful.
- Avoid random holograms, charts, arrows, coins or neon effects unless
  they directly strengthen the actual topic.
- Avoid excessive clutter.
- Avoid sad, dull, lifeless or overly moody styling unless the video's
  real subject requires that emotion.
- Do not use the same layout for every video.
- Choose the composition naturally for the topic.
- Aim for a polished, premium, high-CTR YouTube-thumbnail aesthetic.
- Final aspect ratio: 16:9.

TYPOGRAPHY:
The exact thumbnail headline is:

"{headline}"

Render this exact phrase prominently as part of the finished thumbnail.

Use bold, professional, high-impact YouTube-thumbnail typography.
Choose the placement, scale, font treatment and contrast that work best
with the chosen composition.
The headline should look deliberately designed, not pasted onto the
image afterward.
Do not change or add words.

Do not add any other unnecessary text, captions, logos or watermarks.

Return JSON only:
{{
    "image_prompt": "..."
}}

VIDEO TITLE:
{title}

VIDEO SUMMARY:
{script_summary}

EXACT THUMBNAIL HEADLINE:
{headline}
""".strip()

    result = call_gemini(
        creative_prompt,
        json_mode=True,
    )

    data = _extract_json_object(result.text)

    image_prompt = " ".join(
        str(data.get("image_prompt") or "").split()
    ).strip()

    if not image_prompt:
        raise ValueError(
            "Gemini returned an empty thumbnail image prompt."
        )

    return GeneratedYouTubeThumbnailPrompt(
        headline=headline,
        visual_prompt=_external_thumbnail_prompt(title, headline, image_prompt),
        provider=result.provider,
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
        _local_thumbnail_concept(
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


# THUMBNAIL_PROMPT_GUARANTEED_FALLBACK
def generate_youtube_thumbnail_prompt(
    video_job: Any,
    *,
    publish_title: str | None = None,
) -> GeneratedYouTubeThumbnailPrompt:
    """
    Prefer the full Gemini creative workflow.

    If Gemini returns malformed/empty structured output, retry with
    the simpler Gemini concept generator. If that also fails, always
    return a deterministic topic-specific prompt so completed videos
    never lose the Copy Thumbnail workflow.
    """

    try:
        return _generate_youtube_thumbnail_prompt_ai(
            video_job,
            publish_title=publish_title,
        )
    except Exception as primary_error:
        print(
            "THUMBNAIL_PROMPT_PRIMARY_FAILED:",
            type(primary_error).__name__,
            str(primary_error)[:300],
            flush=True,
        )

    output_directory = Path(
        video_job.output_directory or ""
    )
    package_path = output_directory / "package.json"

    package = {}

    if package_path.is_file():
        try:
            package = json.loads(
                package_path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            package = {}

    title = str(
        publish_title
        or package.get("title")
        or getattr(video_job, "title", "")
        or "YouTube Video"
    ).strip()

    script = str(
        package.get("script")
        or getattr(video_job, "script", "")
        or ""
    ).strip()

    # Simpler Gemini retry.
    if script:
        try:
            headline, visual_prompt, provider = (
                _generate_thumbnail_concept(
                    title=title,
                    script=script,
                )
            )

            return GeneratedYouTubeThumbnailPrompt(
                headline=headline,
                visual_prompt=_external_thumbnail_prompt(
                    title, headline, visual_prompt
                ),
                provider=provider,
            )
        except Exception as retry_error:
            print(
                "THUMBNAIL_PROMPT_RETRY_FAILED:",
                type(retry_error).__name__,
                str(retry_error)[:300],
                flush=True,
            )

    # Final guaranteed fallback.
    headline = _fallback_headline(title)

    visual_prompt = (
        "Create a premium professional 16:9 YouTube thumbnail "
        f"for this exact video topic: {title}. "
        "Build the composition around one dominant, immediately "
        "recognizable topic-specific visual idea. Use meaningful "
        "subjects, objects, environments or phenomena directly "
        "connected to the topic rather than generic stock imagery. "
        "Use strong visual hierarchy, bold subject separation, "
        "clear depth, dramatic but believable lighting, polished "
        "editorial-quality detail, and a clean high-contrast "
        "composition that remains readable at small thumbnail size. "
        f"Render this exact headline prominently: \"{headline}\". "
        "Use deliberate placement, bold professional typography, strong "
        "contrast, and mobile-size readability. Do not change or add words. "
        "Do not add logos, watermarks, UI, borders, random arrows, generic "
        "holograms or meaningless glowing effects."
    )

    return GeneratedYouTubeThumbnailPrompt(
        headline=headline,
        visual_prompt=visual_prompt,
        provider="deterministic-thumbnail-fallback",
    )
