import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.paths import HF_HOME
os.environ.setdefault("HF_HOME", str(HF_HOME))
os.environ.setdefault("XDG_CACHE_HOME", "/home/ubuntu/cache/kokoro")

import numpy as np
import soundfile as sf
from kokoro import KPipeline
from app.voice_registry import get_narrator_voice, validate_voice_speed

SAMPLE_RATE = 24000
TARGET_CHUNK_CHARACTERS = 350
MAX_CHUNK_CHARACTERS = 500
MIN_CHUNK_CHARACTERS = 90
CHUNK_PAUSE_SECONDS = 0.22
PARAGRAPH_PAUSE_SECONDS = 0.55
_pipelines = {}


@dataclass(frozen=True)
class NarrationChunk:
    text: str
    paragraph_break_after: bool = False


def _pipeline_for(language_code: str):
    if language_code not in _pipelines:
        _pipelines[language_code] = KPipeline(lang_code=language_code, repo_id="hexgrad/Kokoro-82M")
    return _pipelines[language_code]


def _sentences(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    protected = re.sub(
        r"\b(Mr|Mrs|Ms|Dr|Prof|St|Sr|Jr|Gen|Sen|Rep|Capt|Lt|Mt)\.\s+(?=[A-Z])",
        lambda match: f"{match.group(1)}.\ue000",
        text,
    )
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“‘(])", protected)
    return [part.replace("\ue000", " ").strip() for part in parts if part.strip()]


def _split_oversized(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARACTERS:
        return [text]
    pieces, remaining = [], text.strip()
    while len(remaining) > MAX_CHUNK_CHARACTERS:
        window = remaining[:MAX_CHUNK_CHARACTERS + 1]
        split_at = max(window.rfind(mark, TARGET_CHUNK_CHARACTERS // 2) for mark in ("; ", ": ", ", ", " — ", " "))
        split_at = MAX_CHUNK_CHARACTERS if split_at < MIN_CHUNK_CHARACTERS else split_at + 1
        pieces.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _pack_paragraph(paragraph: str) -> list[str]:
    units = []
    for sentence in _sentences(paragraph):
        units.extend(_split_oversized(sentence))
    packed, current = [], ""
    for unit in units:
        proposed = f"{current} {unit}".strip()
        if current and len(proposed) > TARGET_CHUNK_CHARACTERS:
            packed.append(current)
            current = unit
        else:
            current = proposed
    if current:
        packed.append(current)
    if len(packed) > 1 and len(packed[-1]) < MIN_CHUNK_CHARACTERS and len(packed[-2]) + len(packed[-1]) + 1 <= MAX_CHUNK_CHARACTERS:
        packed[-2:] = [f"{packed[-2]} {packed[-1]}"]
    return packed


def chunk_narration_text(text: str) -> list[NarrationChunk]:
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n+", str(text or "")) if p.strip()]
    chunks = []
    for paragraph in paragraphs:
        packed = _pack_paragraph(paragraph)
        chunks.extend(NarrationChunk(chunk, i == len(packed) - 1) for i, chunk in enumerate(packed))
    return chunks


def _synthesize_chunk(pipeline, text: str, voice: str, speed: float):
    audio_parts = []
    for _, _, audio in pipeline(text, voice=voice, speed=speed):
        if hasattr(audio, "detach"):
            audio = audio.detach()
        if hasattr(audio, "cpu"):
            audio = audio.cpu()
        part = np.asarray(audio, dtype=np.float32).reshape(-1)
        if part.size:
            audio_parts.append(part)
    if not audio_parts:
        raise RuntimeError("Kokoro produced no audio for a narration chunk.")
    return np.concatenate(audio_parts)


def generate_narration(text, voice, speed, output_path, pipeline_override=None):
    voice_info = get_narrator_voice(voice)
    speed = validate_voice_speed(speed)
    # Normalize all voices to Michael's pace (168 WPM at speed 1.0).
    # Each voice has a correction factor so they all sound the same pace.
    speed = speed * voice_info.speed_correction
    text_chunks = chunk_narration_text(text)
    if not text_chunks:
        raise ValueError("Narration text must not be empty.")
    pipeline = pipeline_override or _pipeline_for(voice_info.pipeline_language)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_suffix(f"{output_path.suffix}.part")
    partial_path.unlink(missing_ok=True)
    try:
        with sf.SoundFile(
            str(partial_path),
            mode="w",
            samplerate=SAMPLE_RATE,
            channels=1,
            subtype="PCM_16",
            format="WAV",
        ) as output:
            for index, chunk in enumerate(text_chunks):
                output.write(_synthesize_chunk(pipeline, chunk.text, voice, speed))
                if index < len(text_chunks) - 1:
                    pause = PARAGRAPH_PAUSE_SECONDS if chunk.paragraph_break_after else CHUNK_PAUSE_SECONDS
                    output.write(np.zeros(round(SAMPLE_RATE * pause), dtype=np.float32))
        partial_path.replace(output_path)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise
    return str(output_path)
