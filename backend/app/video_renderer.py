import shutil
import subprocess
from pathlib import Path


def run(command):
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def render_video(
    scenes,
    narration,
    output_path,
    orientation="landscape",
    captions_file=None,
    burn_captions=False,
    caption_font_size=18,
    caption_position="bottom",
):
    sizes = {
        "landscape": (1920, 1080),
        "portrait": (1080, 1920),
        "square": (1080, 1080),
    }
    width, height = sizes.get(
        orientation, sizes["landscape"]
    )

    output_path = Path(output_path)
    clips = output_path.parent / "render-clips"

    if clips.exists():
        shutil.rmtree(clips)
    clips.mkdir(parents=True)

    timeline = []
    video_types = {".mp4", ".mov", ".webm", ".mkv"}

    for scene in scenes:
        number = int(scene["scene_number"])
        media = Path(scene["media_file"]).resolve()
        clip = clips / f"scene-{number:03d}.mp4"
        duration = max(
            0.5, float(scene["duration_seconds"])
        )

        vf = (
            f"scale={width}:{height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps=30,"
            "format=yuv420p"
        )

        if media.suffix.lower() in video_types:
            command = [
                "ffmpeg", "-y", "-stream_loop", "-1",
                "-i", str(media), "-t", str(duration),
                "-an", "-vf", vf,
            ]
        else:
            command = [
                "ffmpeg", "-y", "-loop", "1",
                "-i", str(media), "-t", str(duration),
                "-an", "-vf", vf,
            ]

        command += [
            "-c:v", "libx264", "-preset", "fast",
            "-crf", "22", "-pix_fmt", "yuv420p",
            str(clip),
        ]
        run(command)
        timeline.append(f"file '{clip.resolve()}'")

    timeline_file = output_path.parent / "timeline.txt"
    timeline_file.write_text("\n".join(timeline))

    video_filter = []

    if burn_captions and captions_file:
        video_filter = [
            "-vf",
            (
                f"subtitles={captions_file}:"
                f"force_style='FontSize={caption_font_size},"
                "Outline=2,"
                "BackColour=&H80000000,"
                "MarginV=60,"
                "Alignment=2'"
            ),
        ]

    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(timeline_file),
        "-i", str(narration),
        "-map", "0:v:0",
        "-map", "1:a:0",
        *video_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ])

    return str(output_path)
