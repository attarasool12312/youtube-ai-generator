import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app.ai_pipeline import (
    generate_video_package,
    generate_video_package_from_script,
)
from app.audio_timing import (
    sync_scenes_to_audio,
    sync_scenes_to_word_timestamps,
)
from app.caption_service import generate_caption_files
from app.database import SessionLocal
from app.models import Avatar, VideoJob
from app.kokoro_service import generate_narration, unload_pipelines
from app.scene_media import download_scene_images
from app.zimage_fallback import (
    apply_zimage_fallbacks,
    start_zimage_warmup,
    cancel_zimage_warmup,
)
from app.video_renderer import render_video, overlay_avatar_video
from app.paths import OUTPUT_ROOT
from app.avatar_service import generate_avatar_video
from app.gpu_utils import can_parallel_render_avatar, log_gpu_status

from app.audio_export import create_narration_mp3

logger = logging.getLogger(__name__)


def _save_job_timings(output_dir, timings):
    timing_path = Path(output_dir) / "timings.json"
    timing_path.write_text(
        json.dumps(timings, indent=2),
        encoding="utf-8",
    )


def _record_timing(
    output_dir,
    timings,
    stage,
    started,
):
    seconds = round(
        time.perf_counter() - started,
        3,
    )

    timings["stages"][stage] = seconds

    _save_job_timings(
        output_dir,
        timings,
    )

    print(
        f"JOB_TIMING {stage}={seconds:.3f}s",
        flush=True,
    )

    return seconds


def _record_timing_seconds(output_dir, timings, stage, seconds):
    seconds = round(float(seconds), 3)
    timings["stages"][stage] = seconds
    _save_job_timings(output_dir, timings)
    print(f"JOB_TIMING {stage}={seconds:.3f}s", flush=True)
    return seconds


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

    total_started = time.perf_counter()

    timings = {
        "job_id": job_id,
        "started_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "stages": {},
    }

    _save_job_timings(
        output_dir,
        timings,
    )

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
        caption_font_family = (
            getattr(job, "caption_font_family", None) or "Manrope"
        )
        caption_font_color = (
            getattr(job, "caption_font_color", None) or "#FFFFFF"
        )
        caption_font_size = job.caption_font_size or 48
        caption_position = job.caption_position or "bottom"
        caption_background = bool(job.caption_background)
        caption_outline = job.caption_outline

        caption_style = {
            "fontFamily": caption_font_family,
            "fontColor": caption_font_color,
            "fontSize": caption_font_size,
            "position": caption_position,
            "background": caption_background,
            "outline": caption_outline,
        }
        avatar_id = job.avatar_id
        avatar_position = (
            job.avatar_position
            or "bottom_right"
        )
        db.commit()

    try:
        progress_callback = lambda status, progress: (
            update_job_progress(
                job_id,
                status,
                progress,
            )
        )

        stage_started = time.perf_counter()

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

        _record_timing(
            output_dir,
            timings,
            "script_and_scene_generation",
            stage_started,
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

        live_research = package.get("live_research")
        research_path = output_dir / "research.json"

        if live_research:
            research_path.write_text(
                json.dumps(live_research, indent=2),
                encoding="utf-8",
            )
        elif research_path.exists():
            research_path.unlink()

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "narration_tts"
                job.progress = 40
                db.commit()

        stage_started = time.perf_counter()

        if narrator_voice.startswith("cloned:"):
            # Use Chatterbox for cloned voice
            cloned_id = int(narrator_voice.split(":")[1])
            import sqlite3
            from app.paths import PROJECT_ROOT
            db_path = PROJECT_ROOT / "backend" / "data" / "app.db"
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cv = conn.execute(
                "SELECT reference_audio_path, exaggeration FROM cloned_voices WHERE id = ?",
                (cloned_id,)
            ).fetchone()
            conn.close()
            if cv is None:
                raise ValueError(f"Cloned voice {cloned_id} not found")
            from app.chatterbox_service import generate_cloned_voice
            generate_cloned_voice(
                text=package["script"],
                reference_audio_path=cv["reference_audio_path"],
                output_path=str(output_dir / "narration.wav"),
                exaggeration=cv["exaggeration"] or 0.5,
            )
        else:
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

        _record_timing(
            output_dir,
            timings,
            "narration_tts",
            stage_started,
        )

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "narration_tts"
                job.progress = 55
                db.commit()

        stage_started = time.perf_counter()

        package["audio_duration_seconds"] = sync_scenes_to_audio(
            package["scenes"],
            output_dir / "narration.wav",
        )
        _record_timing(
            output_dir,
            timings,
            "audio_scene_sync",
            stage_started,
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

        # ========================================================
        # PARALLEL: Captions + Media Download
        # Captions need only audio (available). Media needs
        # scenes (available). Neither needs the other's output.
        # Running both simultaneously saves ~30-60 seconds.
        # ========================================================

        update_job_progress(job_id, "captions", 58)

        captions = None
        caption_started = time.perf_counter()
        media_started = time.perf_counter()
        zimage_warmup = None

        def _release_tts_for_zimage():
            if narrator_voice.startswith("cloned:"):
                try:
                    from app.chatterbox_service import unload_model as unload_chatterbox_model
                    unload_chatterbox_model()
                except Exception as error:
                    logger.info(
                        "ZIMAGE Chatterbox cleanup skipped: %s",
                        error,
                    )
            else:
                try:
                    unload_pipelines()
                except Exception as error:
                    logger.info(
                        "ZIMAGE Kokoro cleanup skipped: %s",
                        error,
                    )

        if captions_enabled:
            def _do_captions():
                return generate_caption_files(
                    audio_path=output_dir / "narration.wav",
                    output_directory=output_dir,
                    orientation=orientation,
                    font_size=caption_font_size,
                    position=caption_position,
                    background=caption_background,
                    outline=caption_outline,
                )

            def _media_progress(completed, total):
                total = max(1, int(total or 0))
                completed = max(0, min(int(completed or 0), total))
                progress = 60 + int((completed / total) * 24)
                update_job_progress(
                    job_id,
                    "media_collection",
                    min(84, max(60, progress)),
                )

            def _do_media():
                return download_scene_images(
                    package["scenes"],
                    output_dir,
                    orientation,
                    media_source,
                    package.get("detected_style", video_style),
                    package.get("title", title),
                    duration_minutes,
                    progress_callback=_media_progress,
                )

            caption_result = [None]
            media_result = [None]
            caption_error = [None]
            media_error = [None]
            caption_elapsed = [0.0]
            media_elapsed = [0.0]

            def _run_captions():
                started = time.perf_counter()
                try:
                    caption_result[0] = _do_captions()
                except Exception as e:
                    caption_error[0] = e
                finally:
                    caption_elapsed[0] = time.perf_counter() - started

            def _run_media():
                started = time.perf_counter()
                try:
                    media_result[0] = _do_media()
                except Exception as e:
                    media_error[0] = e
                finally:
                    media_elapsed[0] = time.perf_counter() - started

            print(
                "PARALLEL_PREP: captions + media download",
                flush=True,
            )

            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_cap = pool.submit(_run_captions)
                fut_med = pool.submit(_run_media)

                # Whisper is normally much faster than stock collection.
                # As soon as Whisper releases the GPU, begin loading
                # Z-Image while stock network searches continue.
                fut_cap.result()

                if caption_error[0]:
                    logger.warning(
                        "Captions failed: %s",
                        caption_error[0],
                    )
                else:
                    captions = caption_result[0]

                    word_timestamps = (
                        captions.pop("word_timestamps", [])
                        if captions
                        else []
                    )

                    try:
                        alignment = sync_scenes_to_word_timestamps(
                            package["scenes"],
                            word_timestamps,
                            package["audio_duration_seconds"],
                        )
                        package["audio_timing_alignment"] = alignment

                        logger.info(
                            "AUDIO_WORD_SYNC applied=%s confidence=%s matched=%s",
                            alignment.get("applied"),
                            alignment.get("confidence"),
                            alignment.get("matched_tokens"),
                        )
                    except Exception as error:
                        logger.warning(
                            "AUDIO_WORD_SYNC fallback to proportional timing: %s",
                            error,
                        )

                _record_timing_seconds(
                    output_dir,
                    timings,
                    "captions",
                    caption_elapsed[0],
                )

                _release_tts_for_zimage()

                zimage_warmup = start_zimage_warmup(
                    output_dir,
                    timeout_seconds=1800,
                )

                if zimage_warmup:
                    logger.info(
                        "ZIMAGE_WARMUP_OVERLAP started during stock collection"
                    )

                fut_med.result()

            if media_error[0]:
                cancel_zimage_warmup(zimage_warmup)
                raise media_error[0]

            package["scenes"] = media_result[0]

            _record_timing_seconds(
                output_dir,
                timings,
                "stock_media_collection",
                media_elapsed[0],
            )

        else:
            # No captions, just download media
            update_job_progress(job_id, "media_collection", 68)

            def _media_only_progress(completed, total):
                total = max(1, int(total or 0))
                completed = max(0, min(int(completed or 0), total))
                progress = 68 + int((completed / total) * 16)
                update_job_progress(
                    job_id,
                    "media_collection",
                    min(84, max(68, progress)),
                )

            _release_tts_for_zimage()
            zimage_warmup = start_zimage_warmup(
                output_dir,
                timeout_seconds=1800,
            )

            package["scenes"] = download_scene_images(
                package["scenes"],
                output_dir,
                orientation,
                media_source,
                package.get("detected_style", video_style),
                package.get("title", title),
                duration_minutes,
                progress_callback=_media_only_progress,
            )

            _record_timing(
                output_dir,
                timings,
                "stock_media_collection",
                media_started,
            )

        zimage_targets = sum(
            1 for scene in package["scenes"]
            if scene.get("needs_ai_fallback")
        )
        if zimage_targets:
            # TTS/Whisper have already been released before speculative
            # Z-Image warmup. Releasing again is harmless and protects the
            # non-warm fallback path.
            _release_tts_for_zimage()

            update_job_progress(job_id, "media_collection", 84)
            zimage_started = time.perf_counter()

            def _zimage_progress(completed, total):
                total = max(1, int(total or 0))
                completed = max(0, min(int(completed or 0), total))
                progress = 84 + int((completed / total) * 4)
                update_job_progress(
                    job_id,
                    "media_collection",
                    min(88, max(84, progress)),
                )

            package["scenes"] = apply_zimage_fallbacks(
                package["scenes"],
                output_dir,
                orientation=orientation,
                video_title=package.get("title", title),
                video_profile=package.get("detected_style", video_style),
                progress_callback=_zimage_progress,
                warmup=zimage_warmup,
            )
            _record_timing(
                output_dir,
                timings,
                "zimage_generation",
                zimage_started,
            )
        else:
            cancel_zimage_warmup(zimage_warmup)
            _record_timing_seconds(
                output_dir,
                timings,
                "zimage_generation",
                0.0,
            )

        package["captions"] = captions

        update_job_progress(job_id, "media_collection", 88)

        # ========================================================
        # GPU-HEAVY STAGES: Render + Avatar
        # Dynamic parallel execution based on available VRAM.
        # Minimum 16GB GPU: runs both simultaneously (~8.5GB used)
        # Larger GPUs: same parallel, more headroom
        # ========================================================

        log_gpu_status()

        # Resolve avatar info before GPU stages
        avatar_cache_id = None
        avatar_source_path = None
        if avatar_id:
            with SessionLocal() as db:
                avatar = db.get(Avatar, avatar_id)
                if avatar is not None and avatar.status == "ready":
                    avatar_cache_id = avatar.cache_id
                    avatar_source_path = avatar.source_path
                else:
                    logger.warning(
                        "Avatar %s unavailable or not ready, skipping",
                        avatar_id,
                    )
                    avatar_id = None

        use_parallel = avatar_id and can_parallel_render_avatar()

        if use_parallel:
            # --- PARALLEL: render + avatar simultaneously ---
            print(
                f"PARALLEL_MODE: render + avatar for job {job_id}",
                flush=True,
            )

            update_job_progress(
                job_id, "final_rendering", 90
            )

            parallel_started = time.perf_counter()
            render_result = [None]
            render_error = [None]
            avatar_result = [None]
            avatar_error = [None]

            def _do_render():
                try:
                    render_result[0] = render_video(
                        package["scenes"],
                        output_dir / "narration.wav",
                        output_dir / "final-video.mp4",
                        orientation,
                        captions_ass=(
                            Path(captions["ass"])
                            if captions
                            else None
                        ),
                        caption_style=caption_style,
                    )
                except Exception as e:
                    render_error[0] = e
                    logger.error("Render failed: %s", e)

            def _do_avatar():
                try:
                    avatar_result[0] = generate_avatar_video(
                        cache_id=avatar_cache_id,
                        source_path=avatar_source_path,
                        narration_path=(
                            output_dir / "narration.wav"
                        ),
                        output_name=f"job_{job_id}",
                    )
                except Exception as e:
                    avatar_error[0] = e
                    logger.warning("Avatar failed: %s", e)

            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_render = pool.submit(_do_render)
                fut_avatar = pool.submit(_do_avatar)
                fut_render.result()
                fut_avatar.result()

            # Record render timing
            render_elapsed = round(
                time.perf_counter() - parallel_started, 3
            )
            timings["stages"]["base_video_render"] = render_elapsed
            _save_job_timings(output_dir, timings)
            print(
                f"JOB_TIMING base_video_render={render_elapsed:.3f}s (parallel)",
                flush=True,
            )

            # Record avatar timing (same wall clock in parallel)
            timings["stages"]["avatar_generation"] = render_elapsed
            _save_job_timings(output_dir, timings)
            print(
                f"JOB_TIMING avatar_generation={render_elapsed:.3f}s (parallel)",
                flush=True,
            )

            if render_error[0]:
                raise render_error[0]

            package["video_file"] = render_result[0] or str(
                output_dir / "final-video.mp4"
            )

            # Avatar overlay (sequential, needs both outputs)
            talking_avatar = avatar_result[0]

            if talking_avatar:
                update_job_progress(
                    job_id, "avatar_overlay", 97
                )

                avatar_overlay_started = time.perf_counter()

                avatar_composite = (
                    output_dir / "final-video-avatar.mp4"
                )

                overlay_avatar_video(
                    base_video=(
                        output_dir / "final-video.mp4"
                    ),
                    avatar_video=talking_avatar,
                    output_path=avatar_composite,
                    position=avatar_position,
                    orientation=orientation,
                )

                _record_timing(
                    output_dir,
                    timings,
                    "avatar_overlay",
                    avatar_overlay_started,
                )

                avatar_composite.replace(
                    output_dir / "final-video.mp4"
                )

                package["video_file"] = str(
                    output_dir / "final-video.mp4"
                )
                package["avatar"] = {
                    "enabled": True,
                    "avatar_id": avatar_id,
                    "position": avatar_position,
                }

                print(
                    "AVATAR_OVERLAY_READY:",
                    job_id,
                    avatar_id,
                    avatar_position,
                    flush=True,
                )
            else:
                package["avatar"] = {
                    "enabled": False,
                    "avatar_id": avatar_id,
                    "position": avatar_position,
                    "error": str(avatar_error[0]) if avatar_error[0] else "no output",
                }
                print(
                    "AVATAR_FAILED_IN_PARALLEL:",
                    str(avatar_error[0]),
                    file=sys.stderr,
                    flush=True,
                )

        else:
            # --- SEQUENTIAL: original flow ---
            with SessionLocal() as db:
                job = db.get(VideoJob, job_id)
                if job:
                    job.status = "final_rendering"
                    job.progress = 90
                    db.commit()

            stage_started = time.perf_counter()

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
                caption_style=caption_style,
            )

            _record_timing(
                output_dir,
                timings,
                "base_video_render",
                stage_started,
            )

            # Avatar is an optional post-render layer.
            # Any avatar failure must leave the normal completed
            # Stock Only video intact.
            if avatar_id:
                try:
                    update_job_progress(
                        job_id,
                        "avatar_generation",
                        93,
                    )

                    avatar_generation_started = (
                        time.perf_counter()
                    )

                    talking_avatar = generate_avatar_video(
                        cache_id=avatar_cache_id,
                        source_path=avatar_source_path,
                        narration_path=(
                            output_dir / "narration.wav"
                        ),
                        output_name=f"job_{job_id}",
                    )

                    _record_timing(
                        output_dir,
                        timings,
                        "avatar_generation",
                        avatar_generation_started,
                    )

                    update_job_progress(
                        job_id,
                        "avatar_overlay",
                        97,
                    )

                    avatar_composite = (
                        output_dir
                        / "final-video-avatar.mp4"
                    )

                    avatar_overlay_started = (
                        time.perf_counter()
                    )

                    overlay_avatar_video(
                        base_video=(
                            output_dir
                            / "final-video.mp4"
                        ),
                        avatar_video=talking_avatar,
                        output_path=avatar_composite,
                        position=avatar_position,
                        orientation=orientation,
                    )

                    _record_timing(
                        output_dir,
                        timings,
                        "avatar_overlay",
                        avatar_overlay_started,
                    )

                    avatar_composite.replace(
                        output_dir
                        / "final-video.mp4"
                    )

                    package["video_file"] = str(
                        output_dir
                        / "final-video.mp4"
                    )
                    package["avatar"] = {
                        "enabled": True,
                        "avatar_id": avatar_id,
                        "position": avatar_position,
                    }

                    print(
                        "AVATAR_OVERLAY_READY:",
                        job_id,
                        avatar_id,
                        avatar_position,
                        flush=True,
                    )

                except Exception as avatar_error:
                    package["avatar"] = {
                        "enabled": False,
                        "avatar_id": avatar_id,
                        "position": avatar_position,
                        "error": str(avatar_error),
                    }

                    print(
                        "AVATAR_OVERLAY_FAILED:",
                        str(avatar_error),
                        file=sys.stderr,
                        flush=True,
                    )

        (output_dir / "scenes.json").write_text(
            json.dumps(package["scenes"], indent=2),
            encoding="utf-8",
        )
        (output_dir / "package.json").write_text(
            json.dumps(package, indent=2),
            encoding="utf-8",
        )

        timings["video_ready_total_seconds"] = round(
            time.perf_counter() - total_started,
            3,
        )
        timings["video_ready_at_utc"] = datetime.now(
            timezone.utc
        ).isoformat()

        _save_job_timings(
            output_dir,
            timings,
        )

        print(
            "JOB_TIMING video_ready_total="
            f"{timings['video_ready_total_seconds']:.3f}s",
            flush=True,
        )

        package["timings"] = timings

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

        # Facebook publishing is an independent post-render layer.
        # Failure here must never change an already completed VideoJob.
        try:
            from app.facebook_auto_publish import (
                prepare_and_queue_facebook_auto_publish,
            )

            facebook_publish_result = (
                prepare_and_queue_facebook_auto_publish(
                    job_id
                )
            )

            print(
                "FACEBOOK_AUTO_PUBLISH_RESULT:",
                facebook_publish_result,
                flush=True,
            )

        except Exception as facebook_publish_error:
            print(
                "FACEBOOK_AUTO_PUBLISH_FAILED:",
                str(facebook_publish_error),
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
