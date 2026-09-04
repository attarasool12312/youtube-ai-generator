from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import VideoJob
from app.paths import OUTPUT_ROOT


ALLOWED_SCENE_EXTENSIONS = {
    ".mp4",
    ".webm",
    ".mov",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def parse_scene_plan(job: VideoJob) -> list[dict[str, Any]]:
    if not job or not job.scene_plan:
        return []

    try:
        data = json.loads(job.scene_plan)
    except (TypeError, json.JSONDecodeError):
        return []

    if not isinstance(data, list):
        return []

    return [
        scene
        for scene in data
        if isinstance(scene, dict)
    ]


def get_portable_job_root(job: VideoJob) -> Path:
    root = OUTPUT_ROOT.resolve()

    portable_root = (root / str(job.id)).resolve()

    if (
        portable_root == root
        or root not in portable_root.parents
    ):
        return portable_root

    if portable_root.is_dir():
        return portable_root

    if job.output_directory:
        stored_root = Path(job.output_directory).resolve()

        if (
            stored_root != root
            and root in stored_root.parents
            and stored_root.is_dir()
        ):
            return stored_root

    return portable_root


def resolve_scene_media(job: VideoJob, scene_number: int) -> Path | None:
    if scene_number < 1:
        return None

    root = OUTPUT_ROOT.resolve()
    job_root = get_portable_job_root(job)
    media_root = (job_root / "media").resolve()

    if media_root == root or root not in media_root.parents or not media_root.is_dir():
        return None

    # Prefer the exact final media winner stored in the scene plan.
    for index, scene in enumerate(parse_scene_plan(job), start=1):
        try:
            number = int(scene.get("scene_number") or index)
        except (TypeError, ValueError):
            number = index
        if number != scene_number:
            continue

        raw = str(scene.get("media_file") or "").strip()
        if raw:
            candidate = Path(raw).resolve()
            if candidate.suffix.lower() in ALLOWED_SCENE_EXTENSIONS and root in candidate.parents and candidate.is_file():
                return candidate

            # Portable restore fallback: preserve path beneath media/.
            marker = "/media/"
            normalized = raw.replace("\\", "/")
            if marker in normalized:
                relative = normalized.split(marker, 1)[1]
                candidate = (media_root / relative).resolve()
                if candidate.suffix.lower() in ALLOWED_SCENE_EXTENSIONS and root in candidate.parents and candidate.is_file():
                    return candidate
        break

    # Legacy fallback for older jobs without media_file in scene_plan.
    scene_prefix = f"scene-{scene_number:03d}"
    for directory in (media_root, media_root / "ai"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob(f"{scene_prefix}.*")):
            resolved = path.resolve()
            if resolved.suffix.lower() in ALLOWED_SCENE_EXTENSIONS and root in resolved.parents and resolved.is_file():
                return resolved

    return None


def prepare_scene_items(
    job: VideoJob,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []

    for index, scene in enumerate(
        parse_scene_plan(job),
        start=1,
    ):
        try:
            scene_number = int(
                scene.get("scene_number") or index
            )
        except (TypeError, ValueError):
            scene_number = index

        media_path = resolve_scene_media(
            job,
            scene_number,
        )

        keywords = scene.get("stock_keywords", [])

        if isinstance(keywords, str):
            keywords = [
                item.strip()
                for item in keywords.split(",")
                if item.strip()
            ]
        elif not isinstance(keywords, list):
            keywords = []

        duration = scene.get("duration_seconds", 0)

        try:
            duration = round(float(duration), 1)
        except (TypeError, ValueError):
            duration = 0

        prepared.append({
            "scene_number": scene_number,
            "narration": str(
                scene.get("narration", "")
            ).strip(),
            "stock_keywords": keywords,
            "image_prompt": str(
                scene.get("image_prompt", "")
            ).strip(),
            "video_prompt": str(
                scene.get("video_prompt", "")
            ).strip(),
            "media_type": str(
                scene.get("media_type", "unknown")
            ).strip().lower(),
            "duration_seconds": duration,
            "provider": str(
                scene.get("media_provider")
                or scene.get("provider")
                or "Unknown"
            ).strip(),
            "source": str(
                scene.get("source", "")
            ).strip(),
            "author": str(
                scene.get("author", "")
            ).strip(),
            "query": str(
                scene.get("query", "")
            ).strip(),
            "media_available": media_path is not None,
            "media_extension": (
                media_path.suffix.lower()
                if media_path
                else ""
            ),
        })

    return prepared
