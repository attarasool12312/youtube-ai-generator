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
