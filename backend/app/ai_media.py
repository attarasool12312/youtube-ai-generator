from hashlib import sha256
from pathlib import Path

from app.local_image_generator import generate_hd_image


def flux_image(prompt, folder, number, orientation):
    folder = Path(folder)
    path = folder / f"scene-{number:03d}-ai.png"

    identity = f"{prompt}|{folder}|{number}"
    seed = int(sha256(identity.encode()).hexdigest()[:8], 16)
    generate_hd_image(
        prompt=prompt,
        output_path=path,
        orientation=orientation,
        seed=seed,
    )

    return {
        "provider": "local_flux",
        "media_type": "image",
        "media_file": str(path),
        "source": f"local-flux://{seed}",
        "author": "Local FLUX.1-schnell",
        "generated": True,
    }
