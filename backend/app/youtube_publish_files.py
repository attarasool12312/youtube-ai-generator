from __future__ import annotations

from pathlib import Path

from app.models import VideoJob
from app.paths import OUTPUT_ROOT


class YouTubePublishFileError(RuntimeError):
    pass


def resolve_final_video_path(job: VideoJob) -> Path:
    if job.status != "completed":
        raise YouTubePublishFileError(
            "Only completed video jobs can be published."
        )

    if not job.output_directory:
        raise YouTubePublishFileError(
            "The video job has no output directory."
        )

    output_root = OUTPUT_ROOT.resolve()
    output_directory = Path(job.output_directory).resolve()

    if (
        output_directory != output_root
        and output_root not in output_directory.parents
    ):
        raise YouTubePublishFileError(
            "The video output directory is outside the allowed root."
        )

    video_path = (
        output_directory / "final-video.mp4"
    ).resolve()

    if output_root not in video_path.parents:
        raise YouTubePublishFileError(
            "The final video path is outside the allowed root."
        )

    if not video_path.is_file():
        raise YouTubePublishFileError(
            "The final-video.mp4 file was not found."
        )

    return video_path
