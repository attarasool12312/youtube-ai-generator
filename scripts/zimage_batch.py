#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

import torch
from diffusers import ZImagePipeline
from PIL import ImageStat

MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"


def dimensions(orientation):
    if orientation == "portrait":
        return 576, 1024
    if orientation == "square":
        return 768, 768
    return 1024, 576



def _image_signature(image):
    gray = image.convert("L").resize((9, 8))
    pixels = list(gray.getdata())
    value = 0

    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            value = (value << 1) | int(left > right)

    return value


def _signature_distance(left, right):
    return (left ^ right).bit_count()


def _image_is_valid(image):
    stat = ImageStat.Stat(image.convert("RGB"))
    return not (
        max(stat.mean) < 1.0
        and max(stat.stddev) < 1.0
    )



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--wait-for-request", type=float, default=0.0)
    parser.add_argument("--ready", default="")
    args = parser.parse_args()

    request_path = Path(args.request)
    result_path = Path(args.result)

    torch.backends.cuda.matmul.allow_tf32 = True

    load_started = time.perf_counter()
    pipe = ZImagePipeline.from_pretrained(
        MODEL_ID,
        dtype=torch.bfloat16,
        low_cpu_mem_usage=False,
        local_files_only=True,
    )
    pipe.to("cuda")
    vae = getattr(pipe, "vae", None)
    if vae is not None:
        if hasattr(vae, "enable_tiling"):
            vae.enable_tiling()
        if hasattr(vae, "enable_slicing"):
            vae.enable_slicing()
    print(f"ZIMAGE_LOAD_SECONDS|{time.perf_counter() - load_started:.3f}", flush=True)

    if args.ready:
        ready_path = Path(args.ready)
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text("ready\n", encoding="utf-8")
        print("ZIMAGE_READY", flush=True)

    if args.wait_for_request > 0 and not request_path.is_file():
        deadline = time.monotonic() + args.wait_for_request
        while not request_path.is_file():
            if time.monotonic() >= deadline:
                print("ZIMAGE_REQUEST_TIMEOUT", flush=True)
                raise SystemExit(3)
            time.sleep(0.25)

    payload = json.loads(request_path.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    width, height = dimensions(str(payload.get("orientation") or "landscape"))

    results = []
    previous_signatures = []
    total = len(items)

    for index, item in enumerate(items, start=1):
        number = int(item["scene_number"])
        output_path = Path(item["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()

        try:
            base_seed = int(item.get("seed") or number)
            base_prompt = str(item["prompt"])

            final_image = None
            final_signature = None
            duplicate_retry = False
            nearest_distance = None

            for generation_attempt in range(2):
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                seed = (
                    base_seed
                    + generation_attempt * 100003
                )

                prompt = base_prompt

                if generation_attempt:
                    duplicate_retry = True
                    prompt += (
                        " Use a clearly different camera position, "
                        "framing, focal arrangement, and composition "
                        "from previous documentary shots while keeping "
                        "the requested subject factually accurate."
                    )

                generator = torch.Generator(
                    device="cuda"
                ).manual_seed(seed)

                image = pipe(
                    prompt=prompt,
                    width=width,
                    height=height,
                    num_inference_steps=9,
                    guidance_scale=0.0,
                    generator=generator,
                ).images[0]

                if not _image_is_valid(image):
                    if generation_attempt == 0:
                        continue
                    raise RuntimeError(
                        "Z-Image generated a blank/black image."
                    )

                signature = _image_signature(image)

                distances = [
                    _signature_distance(signature, previous)
                    for previous in previous_signatures
                ]

                nearest_distance = (
                    min(distances)
                    if distances
                    else None
                )

                final_image = image
                final_signature = signature

                # Retry one time only for extremely close pixel-level
                # duplicates. Different legitimate documentary scenes
                # normally have much larger dHash distances.
                if (
                    nearest_distance is not None
                    and nearest_distance <= 5
                    and generation_attempt == 0
                ):
                    continue

                break

            if final_image is None:
                raise RuntimeError(
                    "Z-Image produced no valid image."
                )

            final_image.save(output_path)

            if final_signature is not None:
                previous_signatures.append(final_signature)

            seconds = round(
                time.perf_counter() - started,
                3,
            )

            results.append({
                "scene_number": number,
                "ok": True,
                "output_path": str(output_path),
                "seconds": seconds,
                "duplicate_retry": duplicate_retry,
                "nearest_dhash_distance": nearest_distance,
            })

        except Exception as error:
            results.append({
                "scene_number": number,
                "ok": False,
                "output_path": str(output_path),
                "error": str(error)[:1000],
            })

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        result_path.write_text(
            json.dumps(
                {"items": results},
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            f"ZIMAGE_PROGRESS|{index}|{total}|{number}",
            flush=True,
        )

    print("ZIMAGE_BATCH_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
