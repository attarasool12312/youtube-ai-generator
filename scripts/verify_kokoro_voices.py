#!/usr/bin/env python3
import argparse
import json
import re
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from app.kokoro_service import SAMPLE_RATE, generate_narration
from app.voice_registry import APPROVED_NARRATOR_VOICES, VOICE_BY_ID

VERIFICATION_TEXTS = {
    "short": "Clear narration helps an audience follow every important idea.",
    "paragraph": (
        "A strong explanation begins with a simple question, then builds the answer one step at a time. "
        "Each sentence should connect naturally to the next, giving the listener enough time to understand the details."
    ),
    "multi_paragraph": (
        "Good narration balances clarity with momentum. It introduces the subject, establishes context, and guides the listener through each stage.\n\n"
        "The next paragraph should feel connected without sounding rushed. A measured pause signals the transition while keeping the larger story moving forward."
    ),
    "punctuation": (
        "Consider three signals: timing, emphasis, and tone. First, does the sentence breathe? Second, can the listener follow a list—without losing the point? Yes; punctuation matters."
    ),
    "long": (
        "Long-form narration needs consistency across many connected sentences. The opening establishes a clear direction, while the middle develops evidence and examples. "
        "A capable narrator should preserve names, numbers, and transitions without suddenly accelerating. When the subject changes, a natural pause helps the audience reset. "
        "The delivery should remain steady through technical explanations, reflective moments, and concise summaries. This final sentence closes the passage at a comfortable pace."
    ),
}
PREVIEW_TEXT = "Welcome. This is a consistent preview of an approved English narration voice for clear, long-form storytelling."


def audio_metrics(path: Path, word_count: int) -> dict:
    audio, sample_rate = sf.read(path, dtype="float32")
    audio = np.asarray(audio).reshape(-1)
    duration = len(audio) / sample_rate if sample_rate else 0.0
    frame = max(1, int(sample_rate * 0.02))
    usable = audio[: len(audio) - (len(audio) % frame)]
    if usable.size:
        rms = np.sqrt(np.mean(usable.reshape(-1, frame) ** 2, axis=1))
        silence_ratio = float(np.mean(rms < 0.003))
    else:
        silence_ratio = 1.0
    return {
        "sample_rate": sample_rate,
        "duration_seconds": round(duration, 3),
        "wpm": round(word_count * 60 / duration, 1) if duration else 0.0,
        "silence_ratio": round(silence_ratio, 3),
        "peak": round(float(np.max(np.abs(audio))), 4) if audio.size else 0.0,
    }


def verify_voice(voice_id: str, output_dir: Path) -> dict:
    cases = {}
    errors = []
    for name, text in VERIFICATION_TEXTS.items():
        target = output_dir / f"{voice_id}-{name}.wav"
        try:
            generate_narration(text, voice_id, 1.0, target)
            metrics = audio_metrics(target, len(re.findall(r"\b[\w']+\b", text)))
            cases[name] = metrics
            if metrics["sample_rate"] != SAMPLE_RATE or metrics["duration_seconds"] <= 0 or metrics["peak"] <= 0:
                errors.append(f"{name}: invalid audio")
            if not 80 <= metrics["wpm"] <= 260:
                errors.append(f"{name}: abnormal pacing {metrics['wpm']} WPM")
            if metrics["silence_ratio"] > 0.65:
                errors.append(f"{name}: extreme silence {metrics['silence_ratio']}")
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    wpms = [case["wpm"] for case in cases.values() if case["wpm"]]
    if len(wpms) > 1 and max(wpms) / min(wpms) > 1.8:
        errors.append("inconsistent pacing across text lengths")
    return {"voice": voice_id, "status": "pass" if not errors else "fail", "errors": errors, "cases": cases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voices", nargs="*", default=list(VOICE_BY_ID))
    parser.add_argument("--output-dir")
    parser.add_argument("--preview-dir")
    parser.add_argument("--json-output")
    args = parser.parse_args()
    unknown = [voice for voice in args.voices if voice not in VOICE_BY_ID]
    if unknown:
        parser.error(f"unapproved voices: {', '.join(unknown)}")
    managed = tempfile.TemporaryDirectory(prefix="kokoro-verify-") if not args.output_dir else None
    output_dir = Path(args.output_dir or managed.name)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = [verify_voice(voice, output_dir) for voice in args.voices]
    if args.preview_dir:
        preview_dir = Path(args.preview_dir)
        preview_dir.mkdir(parents=True, exist_ok=True)
        for result in results:
            if result["status"] == "pass":
                generate_narration(PREVIEW_TEXT, result["voice"], 1.0, preview_dir / f"{result['voice']}.wav")
    report = {"voices": results, "passed": sum(r["status"] == "pass" for r in results), "total": len(results)}
    rendered = json.dumps(report, indent=2)
    if args.json_output:
        Path(args.json_output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if managed:
        managed.cleanup()
    raise SystemExit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
