from pathlib import Path
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import get_db
from app.models import User, VideoJob
from app.job_cleanup import cleanup_job_files
from app.queue_launcher import start_queue_worker
from app.api_settings import has_api_key, save_api_key
from app.security import verify_password

app = FastAPI(title=settings.app_name)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_days * 86400,
    https_only=True,
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

@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    jobs = db.scalars(
        select(VideoJob).order_by(VideoJob.queue_position)
    ).all()
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
    narrator_voice: str = Form("am_michael"),
    voice_pool: list[str] = Form(default=[]),
    voice_speed: float = Form(1.0),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    title_list = [title.strip() for title in titles.splitlines() if title.strip()]
    selected_voices = list(dict.fromkeys([narrator_voice, *voice_pool]))
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
            voice_mode="multi",
            narrator_voice=narrator_voice,
            voice_pool=",".join(selected_voices),
            voice_speed=voice_speed,
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

    root = Path("/workspace/youtube-ai-generator/outputs/jobs").resolve()
    video = (Path(job.output_directory) / "final-video.mp4").resolve()

    if not video.is_file() or root not in video.parents:
        return RedirectResponse("/?success=Video not found.", status_code=303)

    return FileResponse(
        str(video),
        media_type="video/mp4",
        filename=f"video-job-{job.id}.mp4",
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

    return RedirectResponse("/settings?saved=1", status_code=303)
