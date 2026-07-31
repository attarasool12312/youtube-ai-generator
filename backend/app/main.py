from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.paths import OUTPUT_ROOT
from app.config import settings
from app.database import get_db
from app.models import User, VideoJob
from app.job_cleanup import cleanup_job_files
from app.queue_launcher import start_queue_worker
from app.api_settings import (
    has_api_key,
    remove_api_key,
    save_api_key,
)
from app.audio_export import create_narration_mp3
from app.security import verify_password

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_queue_worker()
    yield

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_days * 86400,
    https_only=settings.app_url.lower().startswith("https://"),
    same_site="lax",
)

app.mount("/static", StaticFiles(directory="backend/app/static"), name="static")
templates = Jinja2Templates(directory="backend/app/templates")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/login")
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )




@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid username or password."},
            status_code=400,
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

JOB_STATUS_LABELS = {
    "waiting": "Waiting",
    "processing": "Starting",
    "script_generation": "Script Generation",
    "generating_script": "Script Generation",
    "narration": "Narration",
    "generating_audio": "Narration",
    "media": "Media",
    "generating_media": "Media",
    "captions": "Captions",
    "generating_captions": "Captions",
    "rendering": "Rendering",
    "rendering_video": "Rendering",
    "uploading": "Uploading",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


def prepare_job_display(job):
    job.display_status = JOB_STATUS_LABELS.get(
        job.status,
        job.status.replace("_", " ").title(),
    )

    if job.status == "waiting":
        job.eta_text = "Waiting for previous jobs"
        return job

    if job.status == "completed":
        job.eta_text = "Finished"
        return job

    if job.status in {"failed", "cancelled"}:
        job.eta_text = "Stopped"
        return job

    if not job.started_at or job.progress < 10:
        job.eta_text = "Calculating..."
        return job

    started_at = job.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    elapsed = max(
        1,
        (datetime.now(timezone.utc) - started_at).total_seconds(),
    )
    remaining = elapsed * (
        (100 - max(1, job.progress)) / max(1, job.progress)
    )

    if remaining < 60:
        job.eta_text = "Less than 1 minute"
    elif remaining < 3600:
        minutes = max(1, round(remaining / 60))
        job.eta_text = f"About {minutes} min remaining"
    else:
        hours = int(remaining // 3600)
        minutes = round((remaining % 3600) / 60)
        job.eta_text = (
            f"About {hours}h {minutes}m remaining"
        )

    return job


@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    jobs = db.scalars(
        select(VideoJob).order_by(VideoJob.queue_position)
    ).all()
    jobs = [prepare_job_display(job) for job in jobs]
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "jobs": jobs,
            "error": None,
            "success": request.query_params.get("success"),
        },
    )


@app.post("/jobs")
def add_jobs(
    request: Request,
    titles: str = Form(...),
    duration_minutes: int = Form(10),
    video_style: str = Form("auto"),
    orientation: str = Form("landscape"),
    media_source: str = Form("mixed"),
    narrator_voice: str = Form("am_michael"),
    voice_pool: list[str] = Form(default=[]),
    voice_speed: float = Form(1.0),
    captions_enabled: str = Form("enabled"),
    caption_font_size: int = Form(36),
    caption_position: str = Form("bottom"),
    caption_background: str = Form("on"),
    caption_outline: int = Form(2),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    title_list = [title.strip() for title in titles.splitlines() if title.strip()]
    selected_voices = list(dict.fromkeys([narrator_voice, *voice_pool]))

    captions_enabled_value = captions_enabled == "enabled"
    caption_background_value = caption_background == "on"

    if media_source not in {"stock_only", "ai_only", "mixed"}:
        media_source = "mixed"

    if caption_font_size not in {36, 48, 60}:
        caption_font_size = 48

    if caption_position not in {"top", "middle", "bottom"}:
        caption_position = "bottom"

    if caption_outline not in {0, 1, 2, 4}:
        caption_outline = 2
    if not title_list:
        return RedirectResponse("/?success=No valid titles entered.", status_code=303)
    if len(title_list) > 20:
        return RedirectResponse("/?success=Maximum 20 titles allowed.", status_code=303)

    last_job = db.scalar(
        select(VideoJob).order_by(VideoJob.queue_position.desc()).limit(1)
    )
    next_position = last_job.queue_position + 1 if last_job else 1

    for index, title in enumerate(title_list):
        db.add(VideoJob(
            title=title,
            duration_minutes=duration_minutes,
            video_style=video_style,
            orientation=orientation,
            media_source=media_source,
            voice_mode="multi",
            narrator_voice=narrator_voice,
            voice_pool=",".join(selected_voices),
            voice_speed=voice_speed,
            captions_enabled=captions_enabled_value,
            caption_font_size=caption_font_size,
            caption_position=caption_position,
            caption_background=caption_background_value,
            caption_outline=caption_outline,
            status="waiting",
            progress=0,
            queue_position=next_position + index,
        ))

    db.commit()
    start_queue_worker()
    return RedirectResponse(
        f"/?success={len(title_list)} title(s) added to queue.",
        status_code=303,
    )


@app.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)

    if not job:
        return RedirectResponse(
            "/?success=Job not found.",
            status_code=303,
        )

    if job.status in {"completed", "failed", "cancelled"}:
        return RedirectResponse(
            "/?success=This job cannot be cancelled.",
            status_code=303,
        )

    worker_pid = job.worker_pid
    output_directory = job.output_directory

    job.status = "cancelled"
    job.progress = 0
    job.worker_pid = None
    job.completed_at = datetime.now(timezone.utc)
    job.error_message = "Job cancelled by user."
    db.commit()

    cleanup_job_files(
        worker_pid=worker_pid,
        output_directory=output_directory,
    )

    return RedirectResponse(
        "/?success=Job cancelled.",
        status_code=303,
    )


@app.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)

    if not job:
        return RedirectResponse(
            "/?success=Job not found.",
            status_code=303,
        )

    if job.status not in {"failed", "cancelled"}:
        return RedirectResponse(
            "/?success=Only failed or cancelled jobs can be retried.",
            status_code=303,
        )

    cleanup_job_files(
        worker_pid=job.worker_pid,
        output_directory=job.output_directory,
    )

    last_job = db.scalar(
        select(VideoJob)
        .order_by(VideoJob.queue_position.desc())
        .limit(1)
    )

    job.queue_position = (
        last_job.queue_position + 1
        if last_job and last_job.id != job.id
        else job.queue_position
    )
    job.status = "waiting"
    job.progress = 0
    job.error_message = None
    job.worker_pid = None
    job.started_at = None
    job.completed_at = None
    job.output_directory = None
    job.script = None
    job.scene_plan = None
    db.commit()

    start_queue_worker()

    return RedirectResponse(
        "/?success=Job added back to the queue.",
        status_code=303,
    )


@app.post("/jobs/{job_id}/delete")
def delete_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)

    if not job:
        return RedirectResponse(
            "/?success=Job not found.",
            status_code=303,
        )

    cleanup_job_files(
        worker_pid=job.worker_pid,
        output_directory=job.output_directory,
    )

    db.delete(job)
    db.commit()

    return RedirectResponse(
        "/?success=Job and all related files deleted.",
        status_code=303,
    )


@app.get("/jobs/{job_id}/download")
def download_job_video(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)
    if not job or not job.output_directory:
        return RedirectResponse("/?success=Video not found.", status_code=303)

    root = OUTPUT_ROOT.resolve()
    video = (Path(job.output_directory) / "final-video.mp4").resolve()

    if not video.is_file() or root not in video.parents:
        return RedirectResponse("/?success=Video not found.", status_code=303)

    def iterfile():
        with open(video, "rb") as file:
            while chunk := file.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="video-job-{job.id}.mp4"',
            "Content-Length": str(video.stat().st_size),
        },
    )


@app.get("/settings")
def api_settings_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request,
        "api_settings.html",
        {
            "request": request,
            "pexels_ready": has_api_key("pexels_api_key"),
            "pixabay_ready": has_api_key("pixabay_api_key"),
            "coverr_ready": has_api_key("coverr_api_key"),
            "openverse_id_ready": has_api_key("openverse_client_id"),
            "openverse_secret_ready": has_api_key("openverse_client_secret"),
            "gemini_ready": has_api_key("gemini_api_key"),
            "together_ready": has_api_key("together_api_key"),
        },
    )


@app.post("/settings")
def save_api_settings(
    request: Request,
    pexels_api_key: str = Form(""),
    pixabay_api_key: str = Form(""),
    coverr_api_key: str = Form(""),
    openverse_client_id: str = Form(""),
    openverse_client_secret: str = Form(""),
    gemini_api_key: str = Form(""),
    together_api_key: str = Form(""),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    save_api_key("pexels_api_key", pexels_api_key.strip())
    save_api_key("pixabay_api_key", pixabay_api_key.strip())
    save_api_key("coverr_api_key", coverr_api_key.strip())
    save_api_key("openverse_client_id", openverse_client_id.strip())
    save_api_key(
        "openverse_client_secret",
        openverse_client_secret.strip(),
    )

    save_api_key("gemini_api_key", gemini_api_key.strip())
    save_api_key("together_api_key", together_api_key.strip())

    return RedirectResponse("/settings?saved=1", status_code=303)


ALLOWED_API_SETTINGS = {
    "pexels_api_key",
    "pixabay_api_key",
    "coverr_api_key",
    "openverse_client_id",
    "openverse_client_secret",
    "gemini_api_key",
    "together_api_key",
}


@app.post("/settings/remove")
def remove_api_setting(
    request: Request,
    key_name: str = Form(...),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    if key_name not in ALLOWED_API_SETTINGS:
        return RedirectResponse(
            "/settings?message=Invalid API setting.",
            status_code=303,
        )

    removed = remove_api_key(key_name)

    message = (
        "API setting removed."
        if removed
        else "API setting was not configured."
    )

    return RedirectResponse(
        f"/settings?message={message}",
        status_code=303,
    )


def _safe_job_file(job, filename):
    if not job or not job.output_directory:
        return None
    root = OUTPUT_ROOT.resolve()
    path = (Path(job.output_directory) / filename).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


@app.get("/jobs/{job_id}/download/narration")
def download_job_narration(
    job_id: int, request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    job = db.get(VideoJob, job_id)
    mp3 = _safe_job_file(job, "narration.mp3")
    wav = _safe_job_file(job, "narration.wav")
    if not mp3 and wav:
        try:
            mp3 = create_narration_mp3(
                wav, wav.with_name("narration.mp3")
            )
        except Exception:
            mp3 = None
    if not mp3:
        return RedirectResponse(
            "/?success=Narration not found.", status_code=303
        )
    return FileResponse(
        str(mp3), media_type="audio/mpeg",
        filename=f"narration-job-{job_id}.mp3",
    )


@app.get("/jobs/{job_id}/download/script")
def download_job_script(
    job_id: int, request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    script = _safe_job_file(
        db.get(VideoJob, job_id), "script.txt"
    )
    if not script:
        return RedirectResponse(
            "/?success=Script not found.", status_code=303
        )
    return FileResponse(
        str(script), media_type="text/plain",
        filename=f"script-job-{job_id}.txt",
    )



@app.get("/jobs/{job_id}/download/captions.srt")
def download_job_captions_srt(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    subtitle = _safe_job_file(
        db.get(VideoJob, job_id), "captions.srt"
    )

    if not subtitle:
        return RedirectResponse(
            "/?success=Caption file not found.",
            status_code=303,
        )

    return FileResponse(
        str(subtitle),
        media_type="application/x-subrip",
        filename=f"captions-job-{job_id}.srt",
    )


@app.get("/jobs/{job_id}/download/captions.vtt")
def download_job_captions_vtt(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    subtitle = _safe_job_file(
        db.get(VideoJob, job_id), "captions.vtt"
    )

    if not subtitle:
        return RedirectResponse(
            "/?success=Caption file not found.",
            status_code=303,
        )

    return FileResponse(
        str(subtitle),
        media_type="text/vtt",
        filename=f"captions-job-{job_id}.vtt",
    )
