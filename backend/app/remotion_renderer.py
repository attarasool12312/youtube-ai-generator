import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path


VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".webm", ".mkv", ".m4v"
}


def _project_root():
    return Path(__file__).resolve().parents[2]


def _remotion_root():
    return _project_root() / "remotion"


def _timestamp(value):
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.replace(".", ",").split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis) / 1000
    )


def _parse_srt(path):
    path = Path(path)
    if not path.is_file():
        return []

    text = path.read_text(
        encoding="utf-8",
        errors="ignore",
    ).replace("\r\n", "\n")

    cues = []

    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        timing_index = next(
            (
                i for i, line in enumerate(lines)
                if " --> " in line
            ),
            None,
        )

        if timing_index is None:
            continue

        try:
            start_raw, end_raw = (
                lines[timing_index]
                .split(" --> ", 1)
            )

            cue_text = " ".join(
                line.strip()
                for line in lines[timing_index + 1:]
                if line.strip()
            )

            if cue_text:
                cues.append({
                    "start": _timestamp(start_raw.strip()),
                    "end": _timestamp(end_raw.strip()),
                    "text": cue_text,
                })
        except Exception:
            continue

    return cues


def _link_or_copy(source, target):
    source = Path(source).resolve()
    target = Path(target)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        os.link(source, target)
    except Exception:
        shutil.copy2(source, target)


def render_remotion_video(
    scenes,
    narration,
    output_path,
    orientation="landscape",
    captions_ass=None,
    caption_style=None,
):
    if os.getenv(
        "REMOTION_ENABLED",
        "1",
    ).strip().lower() in {
        "0", "false", "no", "off"
    }:
        return None

    root = _remotion_root()
    cli = root / "node_modules" / ".bin" / "remotion"

    if not cli.is_file():
        return None

    sizes = {
        "landscape": (1920, 1080),
        "portrait": (1080, 1920),
        "square": (1080, 1080),
    }

    width, height = sizes.get(
        orientation,
        sizes["landscape"],
    )

    output_path = Path(output_path).resolve()
    narration = Path(narration).resolve()

    token = (
        output_path.parent.name
        + "-"
        + uuid.uuid4().hex[:8]
    )

    public_runtime = (
        root / "public" / "runtime" / token
    )
    public_runtime.mkdir(
        parents=True,
        exist_ok=True,
    )

    props_path = (
        output_path.parent
        / f".remotion-{token}.json"
    )

    temporary_output = output_path.with_name(
        f".{output_path.stem}.remotion.mp4"
    )
    temporary_output.unlink(
        missing_ok=True,
    )

    remotion_scenes = []

    try:
        for scene in scenes:
            number = int(scene["scene_number"])
            media = Path(
                str(scene.get("media_file") or "")
            ).resolve()

            if not media.is_file():
                raise RuntimeError(
                    f"Remotion scene {number} media missing: {media}"
                )

            suffix = (
                media.suffix.lower()
                if media.suffix
                else ".bin"
            )

            target = (
                public_runtime
                / f"scene-{number:03d}{suffix}"
            )

            _link_or_copy(
                media,
                target,
            )

            remotion_scenes.append({
                "sceneNumber": number,
                "asset": (
                    f"runtime/{token}/"
                    f"{target.name}"
                ),
                "mediaType": (
                    "video"
                    if suffix in VIDEO_EXTENSIONS
                    else "image"
                ),
                "start": float(
                    scene.get("start_seconds") or 0
                ),
                "duration": max(
                    0.05,
                    float(
                        scene.get("duration_seconds")
                        or 0.05
                    ),
                ),
                "subject": str(
                    scene.get("visual_subject") or ""
                ),
                "narration": str(
                    scene.get("narration") or ""
                ),
                "shotType": str(
                    scene.get("shot_type") or ""
                ),
                "cameraAngle": str(
                    scene.get("camera_angle") or ""
                ),
                "action": str(
                    scene.get("action") or ""
                ),
                "environment": str(
                    scene.get("environment") or ""
                ),
                "focalDetail": str(
                    scene.get("focal_detail") or ""
                ),
                "visualDistinction": str(
                    scene.get("visual_distinction") or ""
                ),
            })

        audio_target = (
            public_runtime
            / ("narration" + narration.suffix.lower())
        )

        _link_or_copy(
            narration,
            audio_target,
        )

        captions = []

        if captions_ass:
            srt_path = (
                Path(captions_ass)
                .with_suffix(".srt")
            )
            captions = _parse_srt(srt_path)

        total_seconds = max(
            (
                item["start"] + item["duration"]
                for item in remotion_scenes
            ),
            default=1.0,
        )

        fps = 30

        props = {
            "width": width,
            "height": height,
            "fps": fps,
            "durationInFrames": max(
                1,
                round(total_seconds * fps),
            ),
            "audio": (
                f"runtime/{token}/"
                f"{audio_target.name}"
            ),
            "scenes": remotion_scenes,
            "captions": captions,
            "captionStyle": caption_style or {},
            "orientation": orientation,
        }

        props_path.write_text(
            json.dumps(
                props,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        available_cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
        remotion_concurrency = max(1, available_cpus)

        base_command = [
            str(cli),
            "render",
            "src/index.jsx",
            "ATTA",
            str(temporary_output),
            f"--props={props_path}",
            "--codec=h264",
            f"--concurrency={remotion_concurrency}",
        ]

        # Chromium compositing can use the NVIDIA GPU where supported.
        # Remotion frame generation still uses Chromium/CPU too, so this
        # is acceleration rather than a pure GPU renderer.
        accelerated_command = base_command + [
            "--gl=angle",
            "--x264-preset=veryfast",
        ]

        render_env = os.environ.copy()
        render_env.setdefault(
            "__GLX_VENDOR_LIBRARY_NAME",
            "nvidia",
        )
        render_env.setdefault(
            "LIBGL_ALWAYS_SOFTWARE",
            "0",
        )

        print(
            f"REMOTION_RENDER_START mode=accelerated concurrency={remotion_concurrency}",
            flush=True,
        )

        result = subprocess.run(
            accelerated_command,
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=render_env,
            timeout=max(
                900,
                int(total_seconds * 10),
            ),
        )

        if result.returncode != 0:
            print(
                "REMOTION_ACCELERATION_FALLBACK",
                flush=True,
            )
            temporary_output.unlink(
                missing_ok=True,
            )

            result = subprocess.run(
                base_command,
                cwd=str(root),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(
                    900,
                    int(total_seconds * 10),
                ),
            )

        if result.stdout:
            print(
                result.stdout[-8000:],
                flush=True,
            )

        if result.returncode != 0:
            raise RuntimeError(
                "Remotion render exited "
                f"with code {result.returncode}"
            )

        if (
            not temporary_output.is_file()
            or temporary_output.stat().st_size
            < 10000
        ):
            raise RuntimeError(
                "Remotion produced no valid video."
            )

        temporary_output.replace(
            output_path
        )

        print(
            "REMOTION_RENDER_COMPLETE",
            flush=True,
        )

        return str(output_path)

    finally:
        shutil.rmtree(
            public_runtime,
            ignore_errors=True,
        )
        props_path.unlink(
            missing_ok=True,
        )
        temporary_output.unlink(
            missing_ok=True,
        )
