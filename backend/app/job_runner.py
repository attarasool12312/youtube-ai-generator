import json
import sys
from pathlib import Path

from app.ai_pipeline import generate_video_package
from app.audio_timing import sync_scenes_to_audio
from app.caption_service import generate_caption_files
from app.database import SessionLocal
from app.models import VideoJob
from app.kokoro_service import generate_narration
from app.scene_media import download_scene_images
from app.video_renderer import render_video
from app.paths import OUTPUT_ROOT

from app.audio_export import create_narration_mp3

def run_job(job_id: int) -> None:
    output_dir = OUTPUT_ROOT / str(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        job = db.get(VideoJob, job_id)
        if not job:
            return

        job.output_directory = str(output_dir)
        job.status = "generating_script"
        job.progress = 10
        job.error_message = None

        title = job.title
        duration_minutes = job.duration_minutes
        video_style = job.video_style
        orientation = job.orientation or 'landscape'
        narrator_voice = job.narrator_voice
        voice_speed = job.voice_speed
        captions_enabled = bool(job.captions_enabled)
        caption_font_size = job.caption_font_size or 48
        caption_position = job.caption_position or "bottom"
        caption_background = bool(job.caption_background)
        caption_outline = job.caption_outline
        db.commit()

    try:
        package = generate_video_package(
            title=title,
            duration_minutes=duration_minutes,
            video_style=video_style,
        )


        (output_dir / "script.txt").write_text(
            package["script"], encoding="utf-8"
        )
        (output_dir / "scenes.json").write_text(
            json.dumps(package["scenes"], indent=2), encoding="utf-8"
        )
        (output_dir / "package.json").write_text(
            json.dumps(package, indent=2), encoding="utf-8"
        )

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "generating_audio"
                job.progress = 70
                db.commit()

        generate_narration(
            package["script"],
            narrator_voice,
            voice_speed,
            output_dir / "narration.wav",
        )

        create_narration_mp3(
            output_dir / "narration.wav",
            output_dir / "narration.mp3",
        )

        package["audio_duration_seconds"] = sync_scenes_to_audio(
            package["scenes"],
            output_dir / "narration.wav",
        )

        captions = None

        if captions_enabled:
            with SessionLocal() as db:
                job = db.get(VideoJob, job_id)
                if job:
                    job.status = "generating_captions"
                    job.progress = 76
                    db.commit()

            captions = generate_caption_files(
                audio_path=output_dir / "narration.wav",
                output_directory=output_dir,
                orientation=orientation,
                font_size=caption_font_size,
                position=caption_position,
                background=caption_background,
                outline=caption_outline,
            )

        package["captions"] = captions

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "generating_media"
                job.progress = 82
                db.commit()

        package["scenes"] = download_scene_images(
            package["scenes"], output_dir, orientation
        )

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "rendering_video"
                job.progress = 92
                db.commit()

        package["video_file"] = render_video(
            package["scenes"],
            output_dir / "narration.wav",
            output_dir / "final-video.mp4",
            orientation,
            captions_ass=(
                Path(captions["ass"])
                if captions
                else None
            ),
        )

        (output_dir / "scenes.json").write_text(
            json.dumps(package["scenes"], indent=2),
            encoding="utf-8",
        )
        (output_dir / "package.json").write_text(
            json.dumps(package, indent=2),
            encoding="utf-8",
        )

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if not job:
                return
            job.script = package["script"]
            job.scene_plan = json.dumps(package["scenes"])
            job.status = "completed"
            job.progress = 100
            job.worker_pid = None
            db.commit()

    except Exception as error:
        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(error)
                job.worker_pid = None
                db.commit()
        raise


if __name__ == "__main__":
    run_job(int(sys.argv[1]))
