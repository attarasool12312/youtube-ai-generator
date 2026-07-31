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


def _subtitle_filter(subtitle_path):
    path = str(Path(subtitle_path).resolve())
    path = path.replace("\\", "\\\\")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")

    return (
        f"subtitles=filename='{path}':"
        "fontsdir=/usr/share/fonts/truetype/dejavu"
    )


def render_video(
    scenes, narration, output_path,
    orientation="landscape",
    captions_ass=None,
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

        base_vf = (
            f"scale={width}:{height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )

        fade_duration = min(0.18, duration / 4)
        fade_out_start = max(
            0.0, duration - fade_duration
        )
        fade_vf = (
            f"fade=t=in:st=0:d={fade_duration:.3f},"
            f"fade=t=out:st={fade_out_start:.3f}:"
            f"d={fade_duration:.3f}"
        )

        if media.suffix.lower() in video_types:
            vf = (
                f"{base_vf},fps=30,"
                f"{fade_vf},"
                "format=yuv420p"
            )
            command = [
                "ffmpeg", "-y", "-stream_loop", "-1",
                "-i", str(media), "-t", str(duration),
                "-an", "-vf", vf,
            ]
        else:
            vf = (
                f"{base_vf},fps=30,"
                f"{fade_vf},"
                "format=yuv420p"
            )
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

    final_command = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(timeline_file),
        "-i", str(narration),
        "-map", "0:v:0", "-map", "1:a:0",
    ]

    if captions_ass:
        final_command += [
            "-vf", _subtitle_filter(captions_ass),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
        ]
    else:
        final_command += ["-c:v", "copy"]

    final_command += [
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        run(final_command)
    finally:
        shutil.rmtree(clips, ignore_errors=True)
        timeline_file.unlink(missing_ok=True)

    return str(output_path)
