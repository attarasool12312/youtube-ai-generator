from pathlib import Path

from app.stock_providers import (
    pexels_video, pixabay_video, coverr_video,
    pexels_image, pixabay_image, openverse_image,
)
from app.wikimedia_media import download_image

VIDEO_PROVIDERS = [pexels_video, pixabay_video, coverr_video]
IMAGE_PROVIDERS = [pexels_image, pixabay_image, openverse_image]


def select_media(query, folder, number, requested, orientation):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    errors = []

    if requested == "video":
        for provider in VIDEO_PROVIDERS:
            path = folder / f"scene-{number:03d}.mp4"
            try:
                result = provider(query, path, orientation)
                result["media_file"] = str(path)
                return result
            except Exception as error:
                errors.append(str(error))

    for provider in IMAGE_PROVIDERS:
        path = folder / f"scene-{number:03d}.jpg"
        try:
            result = provider(query, path, orientation)
            result["media_file"] = str(path)
            return result
        except Exception as error:
            errors.append(str(error))

    path = folder / f"scene-{number:03d}.jpg"
    result = download_image(query, path)
    result["media_type"] = "image"
    result["media_file"] = str(path)
    return result
