import json
from pathlib import Path

from app.media_selector import select_media


def download_scene_images(
    scenes, output_dir, orientation="landscape"
):
    media_dir = Path(output_dir) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    attributions = []

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
            )
            scene["media_file"] = result["media_file"]
            scene["media_provider"] = result["provider"]
            scene["media_type"] = result["media_type"]
            scene.pop("media_error", None)

            result["scene_number"] = number
            result["query"] = query
            attributions.append(result)
        except Exception as error:
            scene["media_error"] = str(error)

    report = Path(output_dir) / "attributions.json"
    report.write_text(json.dumps(attributions, indent=2))
    return attributions
