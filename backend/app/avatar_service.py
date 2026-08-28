import os
import shutil
import subprocess
import uuid
from pathlib import Path

import yaml

from app.paths import DATA_ROOT


AVATAR_STORAGE_ROOT = DATA_ROOT / "avatars"

MUSETALK_ROOT = Path(
    os.getenv(
        "MUSETALK_ROOT",
        str(Path.home() / "avatar-engine" / "MuseTalk"),
    )
).resolve()

MUSETALK_CONDA = Path(
    os.getenv(
        "MUSETALK_CONDA",
        str(
            Path.home()
            / "avatar-engine"
            / "miniforge3"
            / "bin"
            / "conda"
        ),
    )
).resolve()

MUSETALK_ENV = os.getenv("MUSETALK_ENV", "musetalk")

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

ALLOWED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
}

ALLOWED_AVATAR_EXTENSIONS = (
    ALLOWED_IMAGE_EXTENSIONS
    | ALLOWED_VIDEO_EXTENSIONS
)


def ensure_avatar_storage():
    AVATAR_STORAGE_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )


def source_type_for_path(path):
    suffix = Path(path).suffix.lower()

    if suffix in ALLOWED_IMAGE_EXTENSIONS:
        return "image"

    if suffix in ALLOWED_VIDEO_EXTENSIONS:
        return "video"

    raise ValueError(
        "Avatar must be a JPG, JPEG, PNG, WEBP, "
        "MP4, MOV, MKV, or WEBM file."
    )


def create_cache_id(user_id):
    return (
        f"user_{int(user_id)}_"
        f"{uuid.uuid4().hex[:16]}"
    )


def avatar_cache_path(cache_id):
    return (
        MUSETALK_ROOT
        / "results"
        / "v15"
        / "avatars"
        / cache_id
    )


def avatar_output_path(cache_id, output_name):
    return (
        avatar_cache_path(cache_id)
        / "vid_output"
        / f"{output_name}.mp4"
    )


def cache_is_ready(cache_id):
    root = avatar_cache_path(cache_id)

    required = (
        root / "coords.pkl",
        root / "latents.pt",
        root / "mask_coords.pkl",
        root / "avator_info.json",
        root / "full_imgs",
        root / "mask",
    )

    return all(path.exists() for path in required)


def _validate_engine():
    if not MUSETALK_ROOT.is_dir():
        raise RuntimeError(
            f"MuseTalk not found at {MUSETALK_ROOT}"
        )

    if not MUSETALK_CONDA.is_file():
        raise RuntimeError(
            f"MuseTalk conda executable not found at "
            f"{MUSETALK_CONDA}"
        )

    if not (
        MUSETALK_ROOT
        / "models"
        / "musetalkV15"
        / "unet.pth"
    ).is_file():
        raise RuntimeError(
            "MuseTalk v1.5 model weights are missing."
        )


def _write_config(
    cache_id,
    source_path,
    preparation,
    audio_clips=None,
):
    ensure_avatar_storage()

    config_path = (
        AVATAR_STORAGE_ROOT
        / f".{cache_id}-{uuid.uuid4().hex}.yaml"
    )

    config = {
        cache_id: {
            "preparation": bool(preparation),
            "bbox_shift": 0,
            "video_path": str(
                Path(source_path).resolve()
            ),
            "audio_clips": audio_clips or {},
        }
    }

    config_path.write_text(
        yaml.safe_dump(
            config,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    return config_path


def _run_realtime(config_path, timeout=None):
    _validate_engine()

    command = [
        str(MUSETALK_CONDA),
        "run",
        "-n",
        MUSETALK_ENV,
        "python",
        "-m",
        "scripts.realtime_inference",
        "--inference_config",
        str(config_path),
        "--result_dir",
        "results/realtime",
        "--unet_model_path",
        "models/musetalkV15/unet.pth",
        "--unet_config",
        "models/musetalkV15/musetalk.json",
        "--version",
        "v15",
        "--fps",
        "25",
        "--ffmpeg_path",
        "/usr/bin",
        "--direct_video",
    ]

    environment = os.environ.copy()
    environment["FFMPEG_PATH"] = "/usr/bin"
    environment["TORCH_HOME"] = str(Path.home() / ".cache" / "torch")

    # MuseTalk uses PyTorch 2.0.1, which does not support
    # the newer CUDA allocator option "expandable_segments".
    environment.pop(
        "PYTORCH_CUDA_ALLOC_CONF",
        None,
    )

    result = subprocess.run(
        command,
        cwd=MUSETALK_ROOT,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        details = (
            (result.stderr or "")
            + "\n"
            + (result.stdout or "")
        ).strip()

        raise RuntimeError(
            "MuseTalk failed:\n"
            + details[-6000:]
        )

    return result


def prepare_avatar(
    cache_id,
    source_path,
    timeout=3600,
):
    source_path = Path(source_path).resolve()

    if not source_path.is_file():
        raise RuntimeError(
            "Avatar source file does not exist."
        )

    source_type_for_path(source_path)

    cache_path = avatar_cache_path(cache_id)

    if cache_path.exists():
        shutil.rmtree(cache_path)

    config_path = _write_config(
        cache_id=cache_id,
        source_path=source_path,
        preparation=True,
    )

    try:
        result = _run_realtime(
            config_path,
            timeout=timeout,
        )
    finally:
        config_path.unlink(missing_ok=True)

    if not cache_is_ready(cache_id):
        error_tail = (
            result.stderr[-3000:]
            if result.stderr
            else ""
        )

        raise RuntimeError(
            "MuseTalk avatar preparation did not "
            f"produce a valid cache. {error_tail}"
        )

    return cache_path


def generate_avatar_video(
    cache_id,
    source_path,
    narration_path,
    output_name,
    timeout=10800,
):
    if not cache_is_ready(cache_id):
        raise RuntimeError(
            "Avatar cache is not ready."
        )

    narration_path = Path(narration_path).resolve()

    if not narration_path.is_file():
        raise RuntimeError(
            "Narration audio does not exist."
        )

    safe_output_name = (
        "avatar_"
        + "".join(
            char
            for char in str(output_name)
            if char.isalnum()
            or char in {"-", "_"}
        )
    )

    config_path = _write_config(
        cache_id=cache_id,
        source_path=source_path,
        preparation=False,
        audio_clips={
            safe_output_name: str(narration_path)
        },
    )

    output_path = avatar_output_path(
        cache_id,
        safe_output_name,
    )

    output_path.unlink(missing_ok=True)

    try:
        result = _run_realtime(
            config_path,
            timeout=timeout,
        )
    finally:
        config_path.unlink(missing_ok=True)

    if not output_path.is_file():
        error_tail = (
            result.stderr[-3000:]
            if result.stderr
            else ""
        )

        raise RuntimeError(
            "MuseTalk did not generate the avatar "
            f"video. {error_tail}"
        )

    return output_path


def delete_avatar_files(cache_id, source_path=None):
    cache_path = avatar_cache_path(cache_id)

    if cache_path.exists():
        shutil.rmtree(
            cache_path,
            ignore_errors=True,
        )

    if source_path:
        source = Path(source_path)

        try:
            source.resolve().relative_to(
                AVATAR_STORAGE_ROOT.resolve()
            )
        except ValueError:
            return

        source.unlink(missing_ok=True)
