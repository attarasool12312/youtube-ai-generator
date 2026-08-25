from __future__ import annotations

import gc
import os
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "PYTORCH_CUDA_ALLOC_CONF",
    "expandable_segments:True",
)

import torch
try:
    from diffusers import FluxPipeline
except ImportError:
    FluxPipeline = None
from PIL import Image

from app.paths import FLUX_MODEL_PATH


MODEL_PATH = FLUX_MODEL_PATH
_PIPELINE: FluxPipeline | None = None
_PIPELINE_PROFILE: str | None = None

TARGET_SIZES = {
    "landscape": (1280, 720),
    "portrait": (720, 1280),
    "square": (1024, 1024),
}

LOW_VRAM_GENERATION_SIZES = {
    "landscape": (768, 432),
    "portrait": (432, 768),
    "square": (640, 640),
}

HIGH_QUALITY_MINIMUM_VRAM_GB = 20.0


def get_gpu_vram_gb() -> float:
    if not torch.cuda.is_available():
        return 0.0

    properties = torch.cuda.get_device_properties(0)

    return round(
        properties.total_memory / (1024 ** 3),
        2,
    )


def get_flux_profile() -> str:
    override = os.getenv(
        "FLUX_QUALITY_PROFILE",
        "auto",
    ).strip().lower()

    if override in {"low", "high"}:
        return override

    return (
        "high"
        if get_gpu_vram_gb()
        >= HIGH_QUALITY_MINIMUM_VRAM_GB
        else "low"
    )


def get_runtime_profile(
    orientation: str = "landscape",
) -> dict[str, Any]:
    orientation = (
        orientation
        if orientation in TARGET_SIZES
        else "landscape"
    )
    profile = get_flux_profile()
    target_size = TARGET_SIZES[orientation]

    generation_size = (
        target_size
        if profile == "high"
        else LOW_VRAM_GENERATION_SIZES[orientation]
    )

    return {
        "profile": profile,
        "gpu_vram_gb": get_gpu_vram_gb(),
        "orientation": orientation,
        "generation_size": generation_size,
        "target_size": target_size,
        "automatic_upscale": (
            generation_size != target_size
        ),
    }


def clear_cuda_memory() -> None:
    gc.collect()

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def release_pipeline() -> None:
    global _PIPELINE
    global _PIPELINE_PROFILE

    if _PIPELINE is not None:
        del _PIPELINE

    _PIPELINE = None
    _PIPELINE_PROFILE = None
    clear_cuda_memory()


def shorten_prompt(
    prompt: str,
    profile: str,
) -> str:
    cleaned = " ".join(str(prompt).split())

    maximum_words = (
        48
        if profile == "low"
        else 70
    )
    maximum_characters = (
        420
        if profile == "low"
        else 650
    )

    words = cleaned.split()

    if len(words) > maximum_words:
        cleaned = " ".join(
            words[:maximum_words]
        )

    if len(cleaned) > maximum_characters:
        cleaned = cleaned[:maximum_characters]
        cleaned = cleaned.rsplit(" ", 1)[0]

    return cleaned.strip(" ,.;:-")


def get_pipeline(
    profile: str | None = None,
) -> FluxPipeline:
    global _PIPELINE
    global _PIPELINE_PROFILE

    profile = profile or get_flux_profile()

    if (
        _PIPELINE is not None
        and _PIPELINE_PROFILE == profile
    ):
        return _PIPELINE

    release_pipeline()

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA GPU is required for local FLUX generation."
        )

    clear_cuda_memory()

    if (
        profile == "high"
        and torch.cuda.is_bf16_supported()
    ):
        torch_dtype = torch.bfloat16
    else:
        torch_dtype = torch.float16

    pipeline = FluxPipeline.from_pretrained(
        str(MODEL_PATH),
        torch_dtype=torch_dtype,
        local_files_only=True,
        low_cpu_mem_usage=True,
    )

    pipeline.vae.enable_tiling()
    pipeline.vae.enable_slicing()

    if hasattr(
        pipeline,
        "enable_attention_slicing",
    ):
        pipeline.enable_attention_slicing(
            "max"
        )

    if profile == "low":
        pipeline.enable_sequential_cpu_offload()
    else:
        pipeline.enable_model_cpu_offload()

    _PIPELINE = pipeline
    _PIPELINE_PROFILE = profile

    return pipeline


def save_image(
    image: Image.Image,
    output_path: Path,
) -> None:
    suffix = output_path.suffix.lower()

    if suffix in {".jpg", ".jpeg"}:
        image.convert("RGB").save(
            output_path,
            format="JPEG",
            quality=95,
            optimize=True,
        )
        return

    image.save(
        output_path,
        format="PNG",
        optimize=True,
    )


def generate_hd_image(
    prompt: str,
    output_path: str | Path,
    orientation: str = "landscape",
    seed: int = 42,
) -> str:
    orientation = (
        orientation
        if orientation in TARGET_SIZES
        else "landscape"
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    runtime = get_runtime_profile(
        orientation
    )
    profile = str(runtime["profile"])
    generation_width, generation_height = (
        runtime["generation_size"]
    )
    target_width, target_height = (
        runtime["target_size"]
    )
    safe_prompt = shorten_prompt(
        prompt,
        profile,
    )

    clear_cuda_memory()

    try:
        pipeline = get_pipeline(profile)

        generator = torch.Generator(
            "cpu"
        ).manual_seed(seed)

        with torch.inference_mode():
            image = pipeline(
                prompt=safe_prompt,
                width=generation_width,
                height=generation_height,
                num_inference_steps=4,
                guidance_scale=0.0,
                generator=generator,
                max_sequence_length=128,
            ).images[0]

        if image.size != (
            target_width,
            target_height,
        ):
            image = image.resize(
                (
                    target_width,
                    target_height,
                ),
                Image.Resampling.LANCZOS,
            )

        save_image(
            image,
            output_path,
        )

        return str(output_path)

    finally:
        clear_cuda_memory()

        # T4-safe development mode:
        # unload FLUX after each image to prevent VRAM buildup.
        if profile == "low":
            release_pipeline()
