import os
from app.paths import HF_HOME
from pathlib import Path

os.environ.setdefault("HF_HOME", str(HF_HOME))
os.environ.setdefault("XDG_CACHE_HOME", "/home/ubuntu/cache/kokoro")

import numpy as np
import soundfile as sf
from kokoro import KPipeline

pipeline = KPipeline(lang_code="a")


def generate_narration(text, voice, speed, output_path):
    chunks = []

    for _, _, audio in pipeline(text, voice=voice, speed=speed):
        if hasattr(audio, "detach"):
            audio = audio.detach()
        if hasattr(audio, "cpu"):
            audio = audio.cpu()
        chunks.append(np.asarray(audio, dtype=np.float32))

    if not chunks:
        raise RuntimeError("Kokoro produced no audio.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sf.write(
        str(output_path),
        np.concatenate(chunks),
        24000,
    )

    return str(output_path)
