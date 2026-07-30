#!/usr/bin/env python3

import subprocess
from pathlib import Path

from app.kokoro_service import generate_narration


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "backend" / "app" / "static" / "voice-previews"

VOICES = {
    "am_michael": "Michael",
    "am_fenrir": "Fenrir",
    "am_puck": "Puck",
    "af_heart": "Heart",
    "af_bella": "Bella",
    "af_nicole": "Nicole",
}

PREVIEW_TEXT = (
    "Welcome to the YouTube AI Video Generator. "
    "This is a short preview of the selected narration voice."
)


def create_preview(voice: str, label: str) -> None:
    target = OUTPUT_DIR / f"{voice}.mp3"

    if target.exists() and target.stat().st_size > 1000:
        print(f"Preserved: {label} -> {target.name}")
        return

    wav_file = OUTPUT_DIR / f"{voice}.wav"

    try:
        generate_narration(
            PREVIEW_TEXT,
            voice,
            1.0,
            wav_file,
        )

        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav_file),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(target),
            ],
            check=True,
        )

        print(f"Generated: {label} -> {target.name}")
    finally:
        wav_file.unlink(missing_ok=True)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for voice, label in VOICES.items():
        create_preview(voice, label)

    print("VOICE_PREVIEWS_COMPLETE")


if __name__ == "__main__":
    main()
