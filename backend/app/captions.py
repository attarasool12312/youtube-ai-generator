from pathlib import Path
import textwrap


def format_time(seconds):
    ms = int((seconds - int(seconds)) * 1000)
    seconds = int(seconds)

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def format_vtt_time(seconds):
    return format_time(seconds).replace(",", ".")


def split_caption(text, max_chars=42):
    lines = textwrap.wrap(
        text.strip(),
        width=max_chars,
        break_long_words=False,
        break_on_hyphens=False,
    )

    if len(lines) <= 2:
        return "\n".join(lines)

    return "\n".join([
        " ".join(lines[:len(lines)//2]),
        " ".join(lines[len(lines)//2:])
    ])


def generate_captions(scenes, output_dir):

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    srt_path = output_dir / "captions.srt"
    vtt_path = output_dir / "captions.vtt"

    srt = []
    vtt = []

    for index, scene in enumerate(scenes, start=1):

        text = split_caption(scene["narration"])

        start = float(scene["start_seconds"])
        end = float(scene["end_seconds"])

        srt.append(
            f"{index}\n"
            f"{format_time(start)} --> {format_time(end)}\n"
            f"{text}\n"
        )

        vtt.append(
            f"{format_vtt_time(start)} --> {format_vtt_time(end)}\n"
            f"{text}\n"
        )

    srt_path.write_text(
        "\n".join(srt),
        encoding="utf-8"
    )

    vtt_path.write_text(
        "WEBVTT\n\n" + "\n".join(vtt),
        encoding="utf-8"
    )

    return {
        "srt": str(srt_path),
        "vtt": str(vtt_path),
    }
