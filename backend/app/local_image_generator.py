from pathlib import Path
from app.paths import FLUX_MODEL_PATH
import gc
import torch
from diffusers import FluxPipeline

MODEL_PATH = FLUX_MODEL_PATH
_PIPELINE = None

SIZES = {
    "landscape": (1280, 720),
    "portrait": (720, 1280),
    "square": (1024, 1024),
}


def get_pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = FluxPipeline.from_pretrained(
            str(MODEL_PATH),
            torch_dtype=torch.bfloat16,
            local_files_only=True,
        )
        _PIPELINE.enable_model_cpu_offload()
        _PIPELINE.vae.enable_tiling()
        _PIPELINE.vae.enable_slicing()
    return _PIPELINE


def generate_hd_image(
    prompt, output_path, orientation="landscape", seed=42
):
    width, height = SIZES.get(
        orientation, SIZES["landscape"]
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generator = torch.Generator("cpu").manual_seed(seed)
    pipe = get_pipeline()
    image = pipe(
        prompt=prompt,
        width=width,
        height=height,
        num_inference_steps=4,
        guidance_scale=0.0,
        generator=generator,
        max_sequence_length=256,
    ).images[0]

    image.save(output_path, quality=95)
    gc.collect()
    torch.cuda.empty_cache()
    return str(output_path)
