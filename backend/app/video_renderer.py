import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


def _stable_fraction(value):
    digest = hashlib.sha256(str(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def _cinematic_motion_enabled():
    value = os.getenv("VIDEO_IMAGE_MOTION", "0").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _transitions_enabled():
    value = os.getenv("VIDEO_TRANSITIONS", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _image_motion_filter(width, height, duration, scene_number):
    """Return a smooth, deterministic Ken Burns style motion filter.

    The source is first enlarged to a 2x working canvas.  The extra sampling
    resolution avoids the integer-pixel stepping that makes basic zoompan
    effects look jerky at 1080p.  Motion is intentionally subtle and uses a
    cosine ease-in/ease-out curve so it never snaps at scene boundaries.
    """
    duration = max(0.5, float(duration))
    total_frames = max(2, int(round(duration * 30.0)))
    end_frame = max(1, total_frames - 1)
    work_width = width * 2
    work_height = height * 2

    # Keep zoom intentionally restrained.  Different scenes get small
    # variations while staying in a documentary-safe 4.5-6.0% range.
    fraction = _stable_fraction(f"motion:{scene_number}")
    zoom_amount = 0.045 + (fraction * 0.015)
    preset = int(fraction * 6.0) % 6
    ease = f"(0.5-0.5*cos(PI*min(on\\,{end_frame})/{end_frame}))"

    if preset == 0:  # slow centered push in
        zoom = f"1+{zoom_amount:.6f}*{ease}"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif preset == 1:  # slow pull out
        zoom = f"1+{zoom_amount:.6f}*(1-{ease})"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif preset == 2:  # left to right drift
        zoom = f"1+{zoom_amount:.6f}*(0.55+0.45*{ease})"
        x = f"(iw-iw/zoom)*(0.18+0.64*{ease})"
        y = "ih/2-(ih/zoom/2)"
    elif preset == 3:  # right to left drift
        zoom = f"1+{zoom_amount:.6f}*(0.55+0.45*{ease})"
        x = f"(iw-iw/zoom)*(0.82-0.64*{ease})"
        y = "ih/2-(ih/zoom/2)"
    elif preset == 4:  # gentle rising diagonal
        zoom = f"1+{zoom_amount:.6f}*(0.60+0.40*{ease})"
        x = f"(iw-iw/zoom)*(0.24+0.52*{ease})"
        y = f"(ih-ih/zoom)*(0.72-0.44*{ease})"
    else:  # gentle falling diagonal
        zoom = f"1+{zoom_amount:.6f}*(0.60+0.40*{ease})"
        x = f"(iw-iw/zoom)*(0.76-0.52*{ease})"
        y = f"(ih-ih/zoom)*(0.28+0.44*{ease})"

    return (
        f"scale={work_width}:{work_height}:"
        "force_original_aspect_ratio=increase:"
        "flags=lanczos,"
        f"crop={work_width}:{work_height},"
        "zoompan="
        f"z='{zoom}':x='{x}':y='{y}':"
        "d=1:"
        f"s={width}x{height}:fps=30,"
        "format=yuv420p"
    )


def _transition_spec(scene, next_scene):
    """Choose a restrained Filmora-style transition deterministically.

    Most boundaries use a cross-dissolve.  Stronger directional transitions
    are deliberately uncommon so the edit does not feel like a template pack.
    """
    if not _transitions_enabled():
        return None

    current_duration = max(0.5, float(scene.get("duration_seconds") or 0.5))
    next_duration = max(0.5, float(next_scene.get("duration_seconds") or 0.5))
    duration = min(0.38, current_duration * 0.12, next_duration * 0.12)
    duration = max(0.12, duration)

    seed = (
        f"{scene.get('scene_number')}|{next_scene.get('scene_number')}|"
        f"{scene.get('media_file', '')}|{next_scene.get('media_file', '')}"
    )
    fraction = _stable_fraction(f"transition:{seed}")

    # 70% cross-dissolve, 20% smooth slides, 10% restrained wipes.
    if fraction < 0.70:
        name = "fade"
    elif fraction < 0.80:
        name = "smoothleft"
    elif fraction < 0.90:
        name = "smoothright"
    elif fraction < 0.95:
        name = "wipeleft"
    else:
        name = "wiperight"

    return {"name": name, "duration": duration}


def _build_transition_plan(scenes):
    plan = {}
    for index, scene in enumerate(scenes[:-1]):
        spec = _transition_spec(scene, scenes[index + 1])
        if spec:
            plan[int(scene["scene_number"])] = spec
    return plan


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


def _build_transition_filter_graph(scenes, transition_plan, captions_ass=None):
    """Build a timing-preserving xfade graph for pre-rendered scene clips.

    Each source clip keeps its original narration-aligned duration.  FFmpeg
    clones only the outgoing final frame for the short transition overlap, so
    the xfade does not shorten the finished timeline or move caption timing.
    """
    if len(scenes) < 2 or not transition_plan:
        return None, None

    parts = []
    labels = {}

    for index, scene in enumerate(scenes):
        number = int(scene["scene_number"])
        spec = transition_plan.get(number)
        if spec and index < len(scenes) - 1:
            label = f"pad{index}"
            parts.append(
                f"[{index}:v]tpad=stop_mode=clone:"
                f"stop_duration={float(spec['duration']):.3f}[{label}]"
            )
            labels[index] = f"[{label}]"
        else:
            labels[index] = f"[{index}:v]"

    current_label = labels[0]
    cumulative_duration = 0.0

    for index in range(1, len(scenes)):
        previous = scenes[index - 1]
        previous_number = int(previous["scene_number"])
        spec = transition_plan.get(previous_number)
        if not spec:
            return None, None

        cumulative_duration += max(
            0.5,
            float(previous.get("duration_seconds") or 0.5),
        )
        output_label = f"xf{index}"
        parts.append(
            f"{current_label}{labels[index]}"
            "xfade="
            f"transition={spec['name']}:"
            f"duration={float(spec['duration']):.3f}:"
            f"offset={cumulative_duration:.3f}"
            f"[{output_label}]"
        )
        current_label = f"[{output_label}]"

    if captions_ass:
        parts.append(
            f"{current_label}{_subtitle_filter(captions_ass)}[vout]"
        )
        current_label = "[vout]"

    return ";\n".join(parts), current_label


def _render_transition_video(
    scenes,
    clip_map,
    narration,
    output_path,
    transition_plan,
    captions_ass,
    width,
    height,
):
    graph, video_label = _build_transition_filter_graph(
        scenes,
        transition_plan,
        captions_ass=captions_ass,
    )
    if not graph or not video_label:
        return False

    output_path = Path(output_path)
    filter_file = output_path.parent / ".transition-filter.txt"
    filter_file.write_text(graph, encoding="utf-8")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
    ]
    for scene in scenes:
        number = int(scene["scene_number"])
        command += ["-i", clip_map[number]]

    audio_index = len(scenes)
    command += [
        "-i",
        str(narration),
        "-filter_complex_script",
        str(filter_file),
        "-map",
        video_label,
        "-map",
        f"{audio_index}:a:0",
    ]

    common_tail = [
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    output_path.unlink(missing_ok=True)

    try:
        if _nvenc_available():
            try:
                _run(command + _nvenc_encoder_arguments() + common_tail)
                _validate_video(output_path, width, height)
                return "h264_nvenc+xfade"
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                RuntimeError,
                OSError,
            ) as error:
                print(
                    "NVENC transition render failed; retrying with CPU. "
                    f"{_error_tail(error)}",
                    flush=True,
                )
                output_path.unlink(missing_ok=True)

        _run(command + _cpu_encoder_arguments() + common_tail)
        _validate_video(output_path, width, height)
        return "libx264+xfade"
    finally:
        filter_file.unlink(missing_ok=True)


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
    transition_plan = _build_transition_plan(scenes)
    video_types = {
        ".mp4",
        ".mov",
        ".webm",
        ".mkv",
    }

    try:
        def _render_one_clip(scene):
            number = int(scene["scene_number"])
            raw_media = str(scene.get("media_file") or "").strip()
            if not raw_media:
                raise RuntimeError(f"Scene {number} has no media_file")
            media = Path(raw_media).resolve()
            if not media.is_file():
                raise RuntimeError(f"Scene {number} media file is missing or invalid: {media}")

            clip = clips / (
                f"scene-{number:03d}.mp4"
            )

            duration = max(
                0.5,
                float(scene["duration_seconds"]),
            )

            is_video = media.suffix.lower() in video_types

            base_vf = (
                f"scale={width}:{height}:"
                "force_original_aspect_ratio="
                "increase,"
                f"crop={width}:{height},"
                "fps=30,"
                "format=yuv420p"
            )

            if not is_video and _cinematic_motion_enabled():
                vf = _image_motion_filter(
                    width,
                    height,
                    duration,
                    number,
                )
            else:
                vf = base_vf

            # When transitions are disabled retain a very short safety fade.
            # With xfade enabled, per-clip fades would create visible dark
            # flashes, so the scene clips stay clean edge-to-edge.
            if not transition_plan:
                fade_duration = min(0.12, duration / 5)
                fade_out_start = max(0.0, duration - fade_duration)
                vf += (
                    f",fade=t=in:st=0:d={fade_duration:.3f},"
                    f"fade=t=out:st={fade_out_start:.3f}:"
                    f"d={fade_duration:.3f}"
                )

            if is_video:
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
                    "-framerate",
                    "30",
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

            # A corrupt input with -stream_loop can encode forever; bound it.
            scene_timeout = max(180.0, float(duration) * 30.0)
            try:
                _run(command, timeout=scene_timeout)
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
                _run(cpu_command, timeout=scene_timeout)

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

        if not clip_map:
            raise RuntimeError(
                "No renderable scenes were provided."
            )

        encoder_used = None
        if transition_plan and len(scenes) > 1:
            try:
                encoder_used = _render_transition_video(
                    scenes=scenes,
                    clip_map=clip_map,
                    narration=narration,
                    output_path=temporary_output,
                    transition_plan=transition_plan,
                    captions_ass=captions_ass,
                    width=width,
                    height=height,
                )
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                RuntimeError,
                OSError,
            ) as error:
                print(
                    "Transition render failed; falling back to clean cuts. "
                    f"{_error_tail(error)}",
                    flush=True,
                )
                temporary_output.unlink(missing_ok=True)
                encoder_used = None

        if not encoder_used:
            for scene in scenes:
                number = int(scene["scene_number"])
                timeline.append(
                    f"file '{clip_map[number]}'"
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

        temporary_output.replace(output_path)

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
