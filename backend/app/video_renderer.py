import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


def _run(command, timeout=None):
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def _cpu_threads():
    cpu_count = os.cpu_count() or 2

    default_limit = max(
        1,
        min(4, max(1, cpu_count // 2)),
    )

    configured = os.getenv(
        "VIDEO_CPU_THREADS",
        "",
    ).strip()

    if configured:
        try:
            default_limit = max(
                1,
                min(cpu_count, int(configured)),
            )
        except ValueError:
            pass

    try:
        load_average = os.getloadavg()[0]
    except (AttributeError, OSError):
        load_average = 0.0

    if load_average >= cpu_count * 0.85:
        return 1

    if load_average >= cpu_count * 0.60:
        return min(default_limit, 2)

    return default_limit


def _cpu_encoder_arguments():
    threads = str(_cpu_threads())

    return [
        "-filter_threads",
        threads,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "22",
        "-threads",
        threads,
        "-pix_fmt",
        "yuv420p",
    ]


def _nvenc_encoder_arguments():
    preset = os.getenv(
        "VIDEO_NVENC_PRESET",
        "p4",
    ).strip().lower()

    if preset not in {
        "p1",
        "p2",
        "p3",
        "p4",
        "p5",
        "p6",
        "p7",
    }:
        preset = "p4"

    quality = os.getenv(
        "VIDEO_NVENC_CQ",
        "22",
    ).strip()

    try:
        quality_number = float(quality)

        if not 0 <= quality_number <= 51:
            quality = "22"
    except ValueError:
        quality = "22"

    return [
        "-filter_threads",
        str(_cpu_threads()),
        "-c:v",
        "h264_nvenc",
        "-preset",
        preset,
        "-tune",
        "hq",
        "-rc",
        "vbr",
        "-cq",
        quality,
        "-b:v",
        "0",
        "-pix_fmt",
        "yuv420p",
    ]


def _encoder_mode():
    mode = os.getenv(
        "VIDEO_ENCODER",
        "auto",
    ).strip().lower()

    if mode not in {"auto", "cpu", "nvenc"}:
        return "auto"

    return mode


@lru_cache(maxsize=1)
def _nvenc_available():
    if _encoder_mode() == "cpu":
        return False

    if not shutil.which("ffmpeg"):
        return False

    smoke_command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x180:r=1",
        "-frames:v",
        "1",
        "-an",
        "-c:v",
        "h264_nvenc",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "null",
        "-",
    ]

    try:
        _run(
            smoke_command,
            timeout=20,
        )
        return True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ):
        return False


def _max_scene_workers():
    """
    How many scene clips to encode in parallel.
    Dynamic based on GPU VRAM and CPU cores.
    Each NVENC session uses ~50MB VRAM.
    Each clip also needs CPU for decode/scale/filter.
    """
    if not _nvenc_available():
        cpu = os.cpu_count() or 2
        return max(1, min(4, cpu // 2))
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        vram_mb = 16000
        if result.returncode == 0:
            vram_mb = int(result.stdout.strip().split("\n")[0].strip())
    except Exception:
        pass
    cpu = os.cpu_count() or 2
    # VRAM limit: each session ~50MB, leave 8GB for avatar
    vram_workers = max(2, (vram_mb - 8000) // 200)
    # CPU limit: each clip needs ~1 core for decode/filter
    cpu_workers = max(2, cpu - 1)
    # Take the minimum of both limits.
    workers = min(vram_workers, cpu_workers)

    # Consumer/GeForce GPUs can hit NVENC session/resource limits
    # long before VRAM is exhausted. Keep parallel scene encoding
    # conservative and allow an explicit override when needed.
    try:
        scene_cap = int(os.getenv("VIDEO_SCENE_WORKERS", "4"))
    except ValueError:
        scene_cap = 4

    scene_cap = max(1, min(scene_cap, 8))
    return min(workers, scene_cap)


def _scene_encoder_arguments():
    """
    Encoder arguments for individual scene clips.
    Uses NVENC when available for massive speedup over CPU.
    """
    if _nvenc_available():
        preset = os.getenv("VIDEO_NVENC_PRESET", "p4").strip().lower()
        if preset not in {"p1", "p2", "p3", "p4", "p5", "p6", "p7"}:
            preset = "p4"
        return [
            "-c:v", "h264_nvenc",
            "-preset", preset,
            "-rc", "constqp",
            "-qp", "20",
            "-pix_fmt", "yuv420p",
        ]
    return _cpu_encoder_arguments()


def _hwaccel_input_args():
    """
    Hardware-accelerated decoding arguments.
    Offloads video decoding from CPU to GPU.
    Returns empty list if not available.
    """
    if _nvenc_available():
        return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    return []


def _subtitle_filter(subtitle_path):
    path = str(Path(subtitle_path).resolve())
    path = path.replace("\\", "\\\\")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")

    return (
        f"subtitles=filename='{path}':"
        "fontsdir=/usr/share/fonts/truetype/dejavu"
    )


def _error_tail(error, line_count=12):
    stderr = getattr(error, "stderr", "") or ""
    lines = stderr.strip().splitlines()

    return "\n".join(lines[-line_count:])


def _validate_video(
    video_path,
    expected_width,
    expected_height,
):
    video_path = Path(video_path)

    if not video_path.is_file():
        raise RuntimeError(
            "Rendered video file was not created."
        )

    if video_path.stat().st_size < 1024:
        raise RuntimeError(
            "Rendered video file is empty or incomplete."
        )

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration:"
                "stream=codec_type,codec_name,"
                "width,height"
            ),
            "-of",
            "json",
            str(video_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if probe.returncode != 0:
        raise RuntimeError(
            "FFprobe validation failed: "
            + probe.stderr.strip()
        )

    try:
        data = json.loads(probe.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "FFprobe returned invalid JSON."
        ) from error

    streams = data.get("streams", [])

    video_stream = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
        ),
        None,
    )

    audio_stream = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "audio"
        ),
        None,
    )

    if not video_stream:
        raise RuntimeError(
            "Rendered output has no video stream."
        )

    if not audio_stream:
        raise RuntimeError(
            "Rendered output has no audio stream."
        )

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)

    if (
        width != expected_width
        or height != expected_height
    ):
        raise RuntimeError(
            "Rendered output has incorrect dimensions: "
            f"{width}x{height}."
        )

    try:
        duration = float(
            data.get("format", {}).get("duration") or 0
        )
    except (TypeError, ValueError):
        duration = 0

    if duration <= 0:
        raise RuntimeError(
            "Rendered output has invalid duration."
        )


def _render_final_video(
    base_command,
    common_tail,
    temporary_output,
    subtitle_path,
    width,
    height,
):
    temporary_output.unlink(missing_ok=True)

    if not subtitle_path:
        command = (
            base_command
            + ["-c:v", "copy"]
            + common_tail
            + [str(temporary_output)]
        )

        _run(command)

        _validate_video(
            temporary_output,
            width,
            height,
        )

        return "stream-copy"

    subtitle_arguments = [
        "-vf",
        _subtitle_filter(subtitle_path),
    ]

    use_nvenc = _nvenc_available()

    if use_nvenc:
        nvenc_command = (
            base_command
            + subtitle_arguments
            + _nvenc_encoder_arguments()
            + common_tail
            + [str(temporary_output)]
        )

        try:
            _run(nvenc_command)

            _validate_video(
                temporary_output,
                width,
                height,
            )

            return "h264_nvenc"

        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            RuntimeError,
            OSError,
        ) as error:
            print(
                "NVENC final render failed; "
                "retrying safely with CPU.",
                flush=True,
            )

            error_tail = _error_tail(error)

            if error_tail:
                print(
                    error_tail,
                    flush=True,
                )

            temporary_output.unlink(
                missing_ok=True
            )

    else:
        print(
            "NVENC unavailable; using CPU "
            "for final caption render.",
            flush=True,
        )

    cpu_command = (
        base_command
        + subtitle_arguments
        + _cpu_encoder_arguments()
        + common_tail
        + [str(temporary_output)]
    )

    _run(cpu_command)

    _validate_video(
        temporary_output,
        width,
        height,
    )

    return "libx264"


def render_video(
    scenes,
    narration,
    output_path,
    orientation="landscape",
    captions_ass=None,
):
    sizes = {
        "landscape": (1920, 1080),
        "portrait": (1080, 1920),
        "square": (1080, 1080),
    }

    width, height = sizes.get(
        orientation,
        sizes["landscape"],
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    clips = output_path.parent / "render-clips"
    timeline_file = (
        output_path.parent / "timeline.txt"
    )
    temporary_output = output_path.with_name(
        f".{output_path.stem}.rendering.mp4"
    )

    if clips.exists():
        shutil.rmtree(clips)

    clips.mkdir(parents=True)

    timeline = []
    video_types = {
        ".mp4",
        ".mov",
        ".webm",
        ".mkv",
    }

    try:
        def _render_one_clip(scene):
            number = int(scene["scene_number"])
            media = Path(
                scene["media_file"]
            ).resolve()

            clip = clips / (
                f"scene-{number:03d}.mp4"
            )

            duration = max(
                0.5,
                float(scene["duration_seconds"]),
            )

            base_vf = (
                f"scale={width}:{height}:"
                "force_original_aspect_ratio="
                "increase,"
                f"crop={width}:{height}"
            )

            fade_duration = min(
                0.18,
                duration / 4,
            )

            fade_out_start = max(
                0.0,
                duration - fade_duration,
            )

            fade_vf = (
                f"fade=t=in:st=0:"
                f"d={fade_duration:.3f},"
                f"fade=t=out:"
                f"st={fade_out_start:.3f}:"
                f"d={fade_duration:.3f}"
            )

            vf = (
                f"{base_vf},"
                "fps=30,"
                f"{fade_vf},"
                "format=yuv420p"
            )

            if (
                media.suffix.lower()
                in video_types
            ):
                command = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(media),
                    "-t",
                    str(duration),
                    "-an",
                    "-vf",
                    vf,
                ]
            else:
                command = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(media),
                    "-t",
                    str(duration),
                    "-an",
                    "-vf",
                    vf,
                ]

            scene_encoder = _scene_encoder_arguments()
            base_command = list(command)
            command += scene_encoder + [str(clip)]

            try:
                _run(command)
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                OSError,
            ) as error:
                if "h264_nvenc" not in scene_encoder:
                    raise

                print(
                    f"NVENC scene {number} failed; "
                    "retrying safely with CPU. "
                    f"{_error_tail(error)}",
                    flush=True,
                )

                clip.unlink(missing_ok=True)

                cpu_command = (
                    base_command
                    + _cpu_encoder_arguments()
                    + [str(clip)]
                )
                _run(cpu_command)

            return number, str(clip.resolve())

        workers = _max_scene_workers()
        print(
            f"Rendering {len(scenes)} scene clips "
            f"with {workers} parallel workers",
            flush=True,
        )

        clip_map = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_render_one_clip, scene): scene
                for scene in scenes
            }
            for future in as_completed(futures):
                number, clip_path = future.result()
                clip_map[number] = clip_path

        for scene in scenes:
            number = int(scene["scene_number"])
            timeline.append(
                f"file '{clip_map[number]}'"
            )

        if not timeline:
            raise RuntimeError(
                "No renderable scenes were provided."
            )

        timeline_file.write_text(
            "\n".join(timeline),
            encoding="utf-8",
        )

        final_base_command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(timeline_file),
            "-i",
            str(narration),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
        ]

        common_tail = [
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
        ]

        encoder_used = _render_final_video(
            base_command=final_base_command,
            common_tail=common_tail,
            temporary_output=temporary_output,
            subtitle_path=captions_ass,
            width=width,
            height=height,
        )

        temporary_output.replace(
            output_path
        )

        print(
            f"Video render completed using "
            f"{encoder_used}.",
            flush=True,
        )

    finally:
        shutil.rmtree(
            clips,
            ignore_errors=True,
        )

        timeline_file.unlink(
            missing_ok=True,
        )

        temporary_output.unlink(
            missing_ok=True,
        )

    return str(output_path)


def overlay_avatar_video(
    base_video,
    avatar_video,
    output_path,
    position="bottom_right",
    orientation="landscape",
):
    """
    Overlay a lip-synced avatar over the already completed video.

    The base video's original audio is preserved. The avatar video's
    audio stream is intentionally ignored.
    """
    sizes = {
        "landscape": (1920, 1080),
        "portrait": (1080, 1920),
        "square": (1080, 1080),
    }

    width, height = sizes.get(
        orientation,
        sizes["landscape"],
    )

    position = (
        position
        if position in {
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        }
        else "bottom_right"
    )

    # Fixed-size circular presenter avatar.
    avatar_width = 220
    margin = 28


    coordinates = {
        "top_left": (
            str(margin),
            str(margin),
        ),
        "top_right": (
            f"W-w-{margin}",
            str(margin),
        ),
        "bottom_left": (
            str(margin),
            f"H-h-{margin}",
        ),
        "bottom_right": (
            f"W-w-{margin}",
            f"H-h-{margin}",
        ),
    }

    x, y = coordinates[position]

    base_video = Path(base_video).resolve()
    avatar_video = Path(avatar_video).resolve()
    output_path = Path(output_path).resolve()

    if not base_video.is_file():
        raise RuntimeError(
            "Base video for avatar overlay is missing."
        )

    if not avatar_video.is_file():
        raise RuntimeError(
            "Generated avatar video is missing."
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_output = output_path.with_name(
        f".{output_path.stem}.avatar-rendering.mp4"
    )

    temporary_output.unlink(
        missing_ok=True,
    )

    # Circular avatar overlay.
    # Generate a static circular mask and loop it, so geq
    # runs once instead of per-frame. Major CPU savings.
    r = avatar_width // 2
    mask_path = output_path.parent / ".circle_mask.png"
    if not mask_path.exists():
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-y", "-f", "lavfi", "-i",
                f"color=white:s={avatar_width}x{avatar_width}:d=1",
                "-vf",
                f"format=rgba,"
                f"geq="
                f"r=255:g=255:b=255:"
                f"a='if(lte("
                f"(X-{r})*(X-{r})+"
                f"(Y-{r})*(Y-{r}),"
                f"{r}*{r}"
                f"),255,0)'",
                "-frames:v", "1",
                str(mask_path),
            ],
            check=True,
            capture_output=True,
        )

    filter_complex = (
        "[1:v]"
        "crop="
        "'min(iw,ih)':"
        "'min(iw,ih)':"
        "'(iw-min(iw,ih))/2':"
        "'max(0,(ih-min(iw,ih))*0.28)',"
        f"scale={avatar_width}:{avatar_width},"
        "format=rgba"
        "[scaled];"
        "[2:v]"
        f"scale={avatar_width}:{avatar_width},"
        "format=rgba,"
        "alphaextract"
        "[amask];"
        "[scaled][amask]alphamerge"
        "[avatar];"
        "[0:v][avatar]"
        f"overlay={x}:{y}:"
        "eof_action=pass:"
        "shortest=0"
        "[video]"
    )


    base_command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(base_video),
        "-i",
        str(avatar_video),
        "-loop", "1",
        "-i",
        str(mask_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[video]",
        "-map",
        "0:a?",
    ]

    common_tail = [
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(temporary_output),
    ]

    if _nvenc_available():
        command = (
            base_command
            + _nvenc_encoder_arguments()
            + common_tail
        )

        try:
            _run(command)
            _validate_video(
                temporary_output,
                width,
                height,
            )

            temporary_output.replace(
                output_path
            )

            return "h264_nvenc"

        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            RuntimeError,
            OSError,
        ):
            temporary_output.unlink(
                missing_ok=True,
            )

    command = (
        base_command
        + _cpu_encoder_arguments()
        + common_tail
    )

    _run(command)

    _validate_video(
        temporary_output,
        width,
        height,
    )

    temporary_output.replace(
        output_path
    )

    return "libx264"
