import subprocess
from pathlib import Path


def create_narration_mp3(wav_path, mp3_path):
    wav_path = Path(wav_path)
    mp3_path = Path(mp3_path)
    subprocess.run([
        "ffmpeg", "-y", "-i", str(wav_path),
        "-codec:a", "libmp3lame", "-q:a", "2",
        str(mp3_path),
    ], check=True, capture_output=True, text=True)
    return mp3_path
