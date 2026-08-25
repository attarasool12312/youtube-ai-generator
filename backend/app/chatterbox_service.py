"""
Chatterbox voice cloning service.

Provides voice cloning from a short reference audio clip (~5-10 seconds).
Uses the Chatterbox TTS model (MIT licensed, by Resemble AI).
Integrates with the existing Kokoro pipeline for video narration.
"""

import logging
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

logger = logging.getLogger(__name__)

# Singleton model instance
_model = None
_model_loading = False

SAMPLE_RATE = 24000

# Chunking config (matches kokoro_service settings)
TARGET_CHUNK_CHARS = 400
MAX_CHUNK_CHARS = 600
CHUNK_PAUSE_SECONDS = 0.15
PARAGRAPH_PAUSE_SECONDS = 0.35


def _get_model():
    """Load Chatterbox model on first call. Cached after that."""
    global _model, _model_loading

    if _model is not None:
        return _model

    if _model_loading:
        raise RuntimeError("Model is currently loading. Try again shortly.")

    _model_loading = True
    try:
        # Patch perth watermarker if needed
        try:
            import perth
            if perth.PerthImplicitWatermarker is None:
                perth.PerthImplicitWatermarker = perth.DummyWatermarker
        except Exception:
            pass

        from chatterbox.tts import ChatterboxTTS

        logger.info("Loading Chatterbox model on CUDA...")
        start = time.time()
        _model = ChatterboxTTS.from_pretrained(device="cuda")
        elapsed = time.time() - start
        logger.info("Chatterbox loaded in %.1fs", elapsed)
        return _model
    except Exception as e:
        logger.error("Failed to load Chatterbox: %s", e)
        raise
    finally:
        _model_loading = False


def unload_model():
    """Free GPU memory by unloading the model."""
    global _model
    if _model is not None:
        del _model
        _model = None
        torch.cuda.empty_cache()
        logger.info("Chatterbox model unloaded")


def _split_text(text: str) -> list[str]:
    """Split long text into chunks for consistent generation."""
    import re
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""

    for sentence in sentences:
        proposed = (current + " " + sentence).strip() if current else sentence
        if len(proposed) > TARGET_CHUNK_CHARS and current:
            chunks.append(current)
            current = sentence
        else:
            current = proposed

        if len(current) > MAX_CHUNK_CHARS:
            chunks.append(current)
            current = ""

    if current:
        chunks.append(current)

    return chunks


def generate_cloned_voice(
    text: str,
    reference_audio_path: str,
    output_path: str,
    exaggeration: float = 0.5,
    cfg_weight: float = 0.5,
) -> str:
    """
    Generate TTS audio using a cloned voice.

    Args:
        text: Text to speak
        reference_audio_path: Path to reference audio (5-30s WAV/MP3)
        output_path: Where to save the output WAV
        exaggeration: Emotion exaggeration (0.0=calm, 1.0=dramatic). Default 0.5.
        cfg_weight: Classifier-free guidance weight. Default 0.5.

    Returns:
        Path to the generated WAV file.
    """
    model = _get_model()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ref_path = str(reference_audio_path)
    chunks = _split_text(text)

    if not chunks:
        raise ValueError("Text must not be empty.")

    logger.info(
        "Generating cloned voice: %d chunks, ref=%s, exag=%.2f",
        len(chunks), Path(ref_path).name, exaggeration,
    )

    start = time.time()
    audio_segments = []
    pause_samples = int(CHUNK_PAUSE_SECONDS * SAMPLE_RATE)
    para_pause_samples = int(PARAGRAPH_PAUSE_SECONDS * SAMPLE_RATE)

    for i, chunk in enumerate(chunks):
        logger.debug("Chunk %d/%d: %d chars", i + 1, len(chunks), len(chunk))
        wav = model.generate(
            chunk,
            audio_prompt_path=ref_path,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
        )

        # wav is a tensor of shape [1, samples]
        audio_np = wav.squeeze(0).cpu().numpy()
        audio_segments.append(audio_np)

        # Add pause between chunks
        if i < len(chunks) - 1:
            is_paragraph = chunk.rstrip().endswith(".")
            pause = para_pause_samples if is_paragraph else pause_samples
            audio_segments.append(np.zeros(pause, dtype=np.float32))

    full_audio = np.concatenate(audio_segments)
    sf.write(str(output_path), full_audio, SAMPLE_RATE)

    elapsed = time.time() - start
    duration = len(full_audio) / SAMPLE_RATE
    logger.info(
        "Cloned voice generated: %.1fs audio in %.1fs (%.1fx realtime)",
        duration, elapsed, duration / elapsed if elapsed > 0 else 0,
    )

    return str(output_path)


def generate_preview(
    reference_audio_path: str,
    output_path: str,
    preview_text: str = None,
) -> str:
    """Generate a short preview clip for a cloned voice."""
    if preview_text is None:
        preview_text = (
            "This is a preview of your cloned voice. "
            "The quality depends on the clarity and length "
            "of your reference audio sample."
        )

    return generate_cloned_voice(
        text=preview_text,
        reference_audio_path=reference_audio_path,
        output_path=output_path,
        exaggeration=0.5,
    )
