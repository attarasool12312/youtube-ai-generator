import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.ai_pipeline import (
    generate_video_package,
    generate_video_package_from_script,
)
from app.audio_timing import sync_scenes_to_audio
from app.caption_service import generate_caption_files
from app.database import SessionLocal
from app.models import VideoJob
from app.kokoro_service import generate_narration
from app.scene_media import download_scene_images
from app.video_renderer import render_video
from app.paths import OUTPUT_ROOT

from app.audio_export import create_narration_mp3

logger = logging.getLogger(__name__)


def update_job_progress(job_id, status, progress):
    with SessionLocal() as db:
        job = db.get(VideoJob, job_id)
        if not job:
            return

        job.status = status
        job.progress = progress
        db.commit()


def run_job(job_id: int) -> None:
    output_dir = OUTPUT_ROOT / str(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        job = db.get(VideoJob, job_id)
        if not job:
            return

        job.output_directory = str(output_dir)
        input_mode = job.input_mode or "title"
        source_script = job.source_script

        job.status = (
            "scene_splitting"
            if input_mode == "script"
            else "outline_generation"
        )
        job.progress = 3
        job.error_message = None

        title = job.title
        duration_minutes = job.duration_minutes
        video_style = job.video_style
        orientation = job.orientation or 'landscape'
        # AI scene media is currently disabled.
        media_source = "stock_only"
        narrator_voice = job.narrator_voice
        voice_speed = job.voice_speed
        captions_enabled = bool(job.captions_enabled)
        caption_font_size = job.caption_font_size or 48
        caption_position = job.caption_position or "bottom"
        caption_background = bool(job.caption_background)
        caption_outline = job.caption_outline
        db.commit()

    try:
        progress_callback = lambda status, progress: (
            update_job_progress(
                job_id,
                status,
                progress,
            )
        )

        if input_mode == "script":
            package = generate_video_package_from_script(
                title=title,
                script=source_script or "",
                duration_minutes=duration_minutes,
                video_style=video_style,
                progress_callback=progress_callback,
            )
        else:
            package = generate_video_package(
                title=title,
                duration_minutes=duration_minutes,
                video_style=video_style,
                progress_callback=progress_callback,
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
                job.status = "narration_tts"
                job.progress = 40
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

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "narration_tts"
                job.progress = 55
                db.commit()

        package["audio_duration_seconds"] = sync_scenes_to_audio(
            package["scenes"],
            output_dir / "narration.wav",
        )
        actual_seconds = package["audio_duration_seconds"]
        words = int(package.get("word_count") or 0)
        wpm = words / (actual_seconds / 60) if actual_seconds > 0 else 0.0
        logger.info(
            "NARRATION target=%.1fs actual=%.1fs words=%d wpm=%.1f voice=%s speed=%s",
            duration_minutes * 60.0, actual_seconds, words, wpm, narrator_voice, voice_speed,
        )
        ratio = actual_seconds / (duration_minutes * 60.0)
        if ratio < 0.82 or ratio > 1.18:
            logger.warning(
                "NARRATION_DURATION_OUTLIER target=%.1fs actual=%.1fs ratio=%.3f",
                duration_minutes * 60.0, actual_seconds, ratio,
            )

        captions = None

        if captions_enabled:
            with SessionLocal() as db:
                job = db.get(VideoJob, job_id)
                if job:
                    job.status = "captions"
                    job.progress = 58
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
            if job and captions_enabled:
                job.status = "captions"
                job.progress = 65
                db.commit()

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "media_collection"
                job.progress = 68
                db.commit()

        package["scenes"] = download_scene_images(
            package["scenes"],
            output_dir,
            orientation,
            media_source,
            package.get("detected_style", video_style),
            package.get("title", title),
            duration_minutes,
        )

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "media_collection"
                job.progress = 86
                db.commit()

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "final_rendering"
                job.progress = 90
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
            job.completed_at = datetime.now(timezone.utc)
            job.worker_pid = None
            db.commit()

        # Generate reusable post-render assets independently from
        # YouTube channel selection. Failures here must never fail
        # an already completed video.
        try:
            from app.youtube_thumbnail_generator import (
                generate_youtube_thumbnail_prompt,
            )

            with SessionLocal() as db:
                asset_job = db.get(VideoJob, job_id)

                if asset_job is not None:
                    thumbnail = generate_youtube_thumbnail_prompt(
                        asset_job
                    )
                    asset_job.thumbnail_headline = (
                        thumbnail.headline
                    )
                    asset_job.thumbnail_prompt = (
                        thumbnail.visual_prompt
                    )
                    asset_job.thumbnail_prompt_provider = (
                        thumbnail.provider
                    )
                    db.commit()

            print(
                "THUMBNAIL_PROMPT_READY:",
                job_id,
                flush=True,
            )

        except Exception as thumbnail_error:
            print(
                "THUMBNAIL_PROMPT_FAILED:",
                str(thumbnail_error),
                file=sys.stderr,
                flush=True,
            )

        try:
            from app.youtube_metadata_generator import (
                generate_youtube_metadata,
            )

            with SessionLocal() as db:
                asset_job = db.get(VideoJob, job_id)

                if (
                    asset_job is not None
                    and asset_job.auto_publish_metadata
                ):
                    metadata = generate_youtube_metadata(
                        asset_job
                    )

                    asset_job.youtube_title = metadata.title
                    asset_job.youtube_description = (
                        metadata.description
                    )
                    asset_job.youtube_tags = ", ".join(
                        metadata.tags
                    )
                    asset_job.youtube_category_id = (
                        metadata.category_id
                    )
                    asset_job.youtube_language = (
                        metadata.language
                    )
                    asset_job.youtube_metadata_provider = (
                        metadata.provider
                    )
                    asset_job.youtube_stock_credits = (
                        metadata.stock_credits
                    )
                    db.commit()

                    print(
                        "YOUTUBE_METADATA_READY:",
                        job_id,
                        metadata.provider,
                        flush=True,
                    )

        except Exception as metadata_error:
            print(
                "YOUTUBE_METADATA_FAILED:",
                str(metadata_error),
                file=sys.stderr,
                flush=True,
            )

        try:
            from app.youtube_auto_publish import (
                prepare_and_queue_auto_publish,
            )

            auto_publish_result = (
                prepare_and_queue_auto_publish(
                    job_id
                )
            )

            print(
                "AUTO_PUBLISH_RESULT:",
                auto_publish_result,
                flush=True,
            )

        except Exception as auto_publish_error:
            print(
                "AUTO_PUBLISH_FAILED:",
                str(auto_publish_error),
                file=sys.stderr,
                flush=True,
            )

    except Exception as error:
        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(error)
                job.completed_at = datetime.now(timezone.utc)
                job.worker_pid = None
                db.commit()
        raise


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_job(int(sys.argv[1]))
