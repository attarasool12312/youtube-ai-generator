import re
from difflib import SequenceMatcher

import soundfile as sf


def sync_scenes_to_audio(scenes, audio_path):
    info = sf.info(str(audio_path))
    total = info.frames / info.samplerate
    planned = sum(float(x["duration_seconds"]) for x in scenes)

    if not scenes or planned <= 0:
        raise ValueError("Invalid scene timing data.")

    cursor = 0.0

    for index, scene in enumerate(scenes):
        duration = total * float(scene["duration_seconds"]) / planned
        end = total if index == len(scenes) - 1 else cursor + duration

        scene["start_seconds"] = round(cursor, 3)
        scene["end_seconds"] = round(end, 3)
        scene["duration_seconds"] = round(end - cursor, 3)
        cursor = end

    return round(total, 3)


def _timing_tokens(value):
    return re.findall(
        r"[a-z0-9]+(?:'[a-z0-9]+)?",
        str(value or "").casefold(),
    )


def sync_scenes_to_word_timestamps(
    scenes,
    word_timestamps,
    total_seconds,
    minimum_confidence=0.55,
):
    """Align scene boundaries to actual spoken Whisper word times."""
    if not scenes or not word_timestamps:
        return {
            "applied": False,
            "confidence": 0.0,
            "reason": "missing scenes or word timestamps",
        }

    narration_tokens = []
    scene_token_ends = []

    for scene in scenes:
        narration_tokens.extend(
            _timing_tokens(scene.get("narration", ""))
        )
        scene_token_ends.append(len(narration_tokens))

    whisper_tokens = []
    whisper_token_to_word = []

    clean_words = []
    for item in word_timestamps:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except Exception:
            continue

        text = str(item.get("word") or "").strip()
        tokens = _timing_tokens(text)
        if not tokens or end < start:
            continue

        word_index = len(clean_words)
        clean_words.append({
            "word": text,
            "start": start,
            "end": end,
        })

        for token in tokens:
            whisper_tokens.append(token)
            whisper_token_to_word.append(word_index)

    if not narration_tokens or not whisper_tokens:
        return {
            "applied": False,
            "confidence": 0.0,
            "reason": "no comparable tokens",
        }

    matcher = SequenceMatcher(
        None,
        narration_tokens,
        whisper_tokens,
        autojunk=False,
    )

    mapping = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapping[block.a + offset] = block.b + offset

    confidence = len(mapping) / max(1, len(narration_tokens))

    if confidence < minimum_confidence:
        return {
            "applied": False,
            "confidence": round(confidence, 4),
            "reason": "whisper transcript match confidence too low",
        }

    total_seconds = float(total_seconds or 0)
    if total_seconds <= 0:
        total_seconds = max(
            float(item["end"])
            for item in clean_words
        )

    boundaries = [0.0]

    for scene_index in range(len(scenes) - 1):
        narration_boundary = scene_token_ends[scene_index]

        next_candidates = [
            index
            for index in mapping
            if index >= narration_boundary
        ]

        previous_candidates = [
            index
            for index in mapping
            if index < narration_boundary
        ]

        boundary = None

        if next_candidates:
            narration_index = min(next_candidates)
            whisper_token_index = mapping[narration_index]
            word_index = whisper_token_to_word[
                whisper_token_index
            ]
            boundary = clean_words[word_index]["start"]

        elif previous_candidates:
            narration_index = max(previous_candidates)
            whisper_token_index = mapping[narration_index]
            word_index = whisper_token_to_word[
                whisper_token_index
            ]
            boundary = clean_words[word_index]["end"]

        if boundary is None:
            boundary = (
                total_seconds
                * narration_boundary
                / max(1, len(narration_tokens))
            )

        boundary = max(
            boundaries[-1] + 0.05,
            min(float(boundary), total_seconds),
        )
        boundaries.append(boundary)

    boundaries.append(total_seconds)

    for index, scene in enumerate(scenes):
        start = boundaries[index]
        end = boundaries[index + 1]

        scene["start_seconds"] = round(start, 3)
        scene["end_seconds"] = round(end, 3)
        scene["duration_seconds"] = round(
            max(0.05, end - start),
            3,
        )

    return {
        "applied": True,
        "confidence": round(confidence, 4),
        "matched_tokens": len(mapping),
        "narration_tokens": len(narration_tokens),
        "whisper_tokens": len(whisper_tokens),
    }
