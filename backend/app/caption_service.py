from __future__ import annotations

import gc
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from app.paths import WHISPER_MODEL_PATH


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    text: str


ORIENTATION_SIZES = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "square": (1080, 1080),
}

LINE_CHAR_LIMITS = {
    "landscape": 34,
    "portrait": 22,
    "square": 28,
}


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)

    return (
        f"{hours:02d}:{minutes:02d}:{secs:02d},"
        f"{milliseconds:03d}"
    )


def _vtt_timestamp(seconds: float) -> str:
    return _srt_timestamp(seconds).replace(",", ".")


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centiseconds = divmod(remainder, 100)

    return (
        f"{hours:d}:{minutes:02d}:{secs:02d}."
        f"{centiseconds:02d}"
    )


def _normalise_text(value: str) -> str:
    return " ".join(value.replace("\n", " ").split()).strip()


def _segment_tokens(segment):
    words = list(getattr(segment, "words", None) or [])

    if words:
        tokens = []

        for word in words:
            text = _normalise_text(word.word or "")

            if not text:
                continue

            start = (
                float(word.start)
                if word.start is not None
                else float(segment.start)
            )
            end = (
                float(word.end)
                if word.end is not None
                else max(start + 0.2, float(segment.end))
            )

            tokens.append((start, max(end, start + 0.05), text))

        return tokens

    text = _normalise_text(segment.text or "")

    if not text:
        return []

    raw_words = text.split()
    start = float(segment.start)
    end = max(start + 0.2, float(segment.end))
    step = (end - start) / max(1, len(raw_words))

    return [
        (
            start + (index * step),
            start + ((index + 1) * step),
            word,
        )
        for index, word in enumerate(raw_words)
    ]


def _build_cues(segments, orientation: str) -> list[CaptionCue]:
    line_limit = LINE_CHAR_LIMITS.get(orientation, 34)
    max_characters = line_limit * 2
    cues: list[CaptionCue] = []
    current = []

    def flush():
        nonlocal current

        if not current:
            return

        text = _normalise_text(
            " ".join(item[2] for item in current)
        )

        if text:
            cues.append(
                CaptionCue(
                    start=current[0][0],
                    end=max(current[-1][1], current[0][0] + 0.3),
                    text=text,
                )
            )

        current = []

    for segment in segments:
        for token in _segment_tokens(segment):
            candidate = _normalise_text(
                " ".join(
                    [item[2] for item in current] + [token[2]]
                )
            )

            duration = (
                token[1] - current[0][0]
                if current
                else token[1] - token[0]
            )

            should_flush = current and (
                len(candidate) > max_characters
                or len(current) >= 10
                or duration > 4.0
            )

            if should_flush:
                flush()

            current.append(token)

            if (
                token[2].endswith((".", "!", "?"))
                and len(current) >= 4
            ):
                flush()

    flush()

    return cues


def _wrapped_text(text: str, orientation: str) -> str:
    width = LINE_CHAR_LIMITS.get(orientation, 34)

    lines = textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    )

    return "\n".join(lines)


def _write_srt(path: Path, cues, orientation: str):
    blocks = []

    for index, cue in enumerate(cues, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    (
                        f"{_srt_timestamp(cue.start)} --> "
                        f"{_srt_timestamp(cue.end)}"
                    ),
                    _wrapped_text(cue.text, orientation),
                ]
            )
        )

    path.write_text(
        "\n\n".join(blocks) + "\n",
        encoding="utf-8",
    )


def _write_vtt(path: Path, cues, orientation: str):
    blocks = ["WEBVTT"]

    for cue in cues:
        blocks.append(
            "\n".join(
                [
                    (
                        f"{_vtt_timestamp(cue.start)} --> "
                        f"{_vtt_timestamp(cue.end)}"
                    ),
                    _wrapped_text(cue.text, orientation),
                ]
            )
        )

    path.write_text(
        "\n\n".join(blocks) + "\n",
        encoding="utf-8",
    )


def _write_ass(
    path: Path,
    cues,
    orientation: str,
    font_size: int,
    position: str,
    background: bool,
    outline: int,
):
    width, height = ORIENTATION_SIZES.get(
        orientation,
        ORIENTATION_SIZES["landscape"],
    )

    scale = width / 1080
    effective_font_size = max(28, round(font_size * scale))
    effective_outline = max(0, round(outline * scale))

    alignment = {
        "top": 8,
        "middle": 5,
        "bottom": 2,
    }.get(position, 2)

    horizontal_margin = max(48, round(width * 0.065))
    vertical_margin = max(60, round(height * 0.07))

    ass_outline = effective_outline
    box_padding = max(4, round(5 * scale))
    back_colour = "&H70000000"

    font_name = os.getenv(
        "YOUTUBE_AI_CAPTION_FONT",
        "DejaVu Sans",
    )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CaptionBox,{font_name},{effective_font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,{back_colour},-1,0,0,0,100,100,0,0,3,{box_padding},0,{alignment},{horizontal_margin},{horizontal_margin},{vertical_margin},1
Style: Caption,{font_name},{effective_font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HFF000000,-1,0,0,0,100,100,0,0,1,{ass_outline},0,{alignment},{horizontal_margin},{horizontal_margin},{vertical_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    for cue in cues:
        text = _wrapped_text(
            cue.text,
            orientation,
        ).replace("\\", "\\\\")
        text = text.replace("{", "\\{").replace("}", "\\}")
        text = text.replace("\n", "\\N")

        if background:
            events.append(
                "Dialogue: 0,"
                f"{_ass_timestamp(cue.start)},"
                f"{_ass_timestamp(cue.end)},"
                f"CaptionBox,,0,0,0,,{text}"
            )

        events.append(
            "Dialogue: 1,"
            f"{_ass_timestamp(cue.start)},"
            f"{_ass_timestamp(cue.end)},"
            f"Caption,,0,0,0,,{text}"
        )

    path.write_text(
        header + "\n".join(events) + "\n",
        encoding="utf-8",
    )


def generate_caption_files(
    audio_path,
    output_directory,
    orientation="landscape",
    font_size=48,
    position="bottom",
    background=True,
    outline=2,
):
    audio_path = Path(audio_path)
    output_directory = Path(output_directory)

    if not audio_path.is_file():
        raise FileNotFoundError(
            f"Caption audio not found: {audio_path}"
        )

    output_directory.mkdir(parents=True, exist_ok=True)
    WHISPER_MODEL_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (WHISPER_MODEL_PATH / "model.bin").is_file():
        model_source = str(WHISPER_MODEL_PATH)
    else:
        model_source = "small.en"

    model = WhisperModel(
        model_source,
        device="cuda",
        compute_type="float16",
        download_root=str(WHISPER_MODEL_PATH.parent),
    )

    segment_iterator, information = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    segments = list(segment_iterator)

    word_timestamps = []
    for segment in segments:
        for word in (getattr(segment, "words", None) or []):
            text = str(getattr(word, "word", "") or "").strip()
            start = getattr(word, "start", None)
            end = getattr(word, "end", None)

            if text and start is not None and end is not None:
                word_timestamps.append({
                    "word": text,
                    "start": round(float(start), 3),
                    "end": round(float(end), 3),
                })

    del model
    gc.collect()

    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

    cues = _build_cues(segments, orientation)

    if not cues:
        raise RuntimeError(
            "Whisper produced no usable caption cues."
        )

    srt_path = output_directory / "captions.srt"
    vtt_path = output_directory / "captions.vtt"
    ass_path = output_directory / "captions.ass"

    _write_srt(srt_path, cues, orientation)
    _write_vtt(vtt_path, cues, orientation)
    _write_ass(
        ass_path,
        cues,
        orientation,
        font_size,
        position,
        background,
        outline,
    )

    return {
        "enabled": True,
        "language": information.language or "en",
        "cue_count": len(cues),
        "srt": str(srt_path),
        "vtt": str(vtt_path),
        "ass": str(ass_path),
        "font_size": font_size,
        "position": position,
        "background": bool(background),
        "outline": outline,
        "word_timestamps": word_timestamps,
    }
