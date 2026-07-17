import json
from pathlib import Path

from app.media_selector import select_media
from app.ai_media import flux_image


def download_scene_images(
    scenes, output_dir, orientation="landscape"
):
    media_dir = Path(output_dir) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    attributions = []
    history_path = Path(
        "/workspace/youtube-ai-generator/"
        "backend/data/media_history.json"
    )

    try:
        history = json.loads(history_path.read_text())
    except Exception:
        history = []

    used_sources = set(history[-500:])
    new_sources = []

    for scene in scenes:
        number = int(scene["scene_number"])
        keywords = scene.get("stock_keywords") or []
        query = " ".join(keywords[:4])
        query = query or scene["narration"][:100]

        try:
            result = select_media(
                query, media_dir, number,
                scene.get("media_type", "video"),
                orientation,
                used_sources,
                scene.get("image_prompt") or query,
            )
            scene["media_file"] = result["media_file"]
            scene["media_provider"] = result["provider"]
            scene["media_type"] = result["media_type"]
            scene.pop("media_error", None)

            result["scene_number"] = number
            result["query"] = query
            scene.update(result)
            attributions.append(result)

            source = result.get("source")
            if source:
                used_sources.add(source)
                new_sources.append(source)
        except Exception as error:
            print(f"Scene {number} stock failed: {error}")
            result = flux_image(
                scene.get("image_prompt") or query,
                media_dir, number, orientation,
            )
            scene.update(result)
            attributions.append(result)

    history_path.parent.mkdir(parents=True, exist_ok=True)
    combined = (history + new_sources)[-1000:]
    history_path.write_text(
        json.dumps(combined, indent=2)
    )

    history_path.parent.mkdir(parents=True, exist_ok=True)
    combined = (history + new_sources)[-1000:]
    history_path.write_text(
        json.dumps(combined, indent=2)
    )

    report = Path(output_dir) / "attributions.json"
    report.write_text(json.dumps(attributions, indent=2))
    return scenes
