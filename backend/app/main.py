from pathlib import Path
import json
from datetime import datetime, timezone
import re
import shlex
import subprocess
from contextlib import asynccontextmanager
import logging
import shutil
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Request, UploadFile
from urllib.parse import quote
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.paths import OUTPUT_ROOT, PROJECT_ROOT
from app.avatar_service import (
    AVATAR_STORAGE_ROOT,
    create_cache_id,
    delete_avatar_files,
    ensure_avatar_storage,
    prepare_avatar,
    source_type_for_path,
)
from app.download_filenames import content_disposition_for_video
from app.config import settings
from app.database import SessionLocal, get_db
from app.dashboard_metrics import collect_dashboard_metrics
from app.project_info import get_project_info
from app.publishing_status import collect_publishing_status
from app.provider_quota import clear_quota_cache
from app.youtube_oauth import (
    YouTubeOAuthConfigurationError,
    build_youtube_oauth_flow,
    create_youtube_authorization_url,
    get_public_app_url,
    normalize_public_app_url,
)
from app.youtube_connections import (
    ensure_channel_thumbnail_cached,
    save_connected_channel,
)
from app.youtube_categories import YOUTUBE_CATEGORY_OPTIONS
from app.youtube_thumbnail_generator import (
    generate_youtube_thumbnail_prompt,
)
from app.title_research import research_titles
from app.youtube_publish_drafts import save_youtube_publish_draft
from app.youtube_publish_files import (
    YouTubePublishFileError,
    resolve_final_video_path,
)
from app.youtube_publish_validation import (
    YouTubePublishValidationError,
)
from app.scene_library import prepare_scene_items, resolve_scene_media
from app.youtube_publish_queue import (
    YouTubePublishQueueError,
    queue_youtube_publish_job,
)
from app.models import (
    User,
    VideoJob,
    YouTubeConnection,
    YouTubePublishJob,
    FacebookConnection,
    FacebookPublishJob,
    TitleResearchProfile,
    Avatar,
)
from app.job_cleanup import cleanup_job_files
from app.queue_launcher import start_queue_worker
from app.youtube_publish_launcher import (
    start_youtube_publish_worker,
)
from app.facebook_publish_launcher import (
    start_facebook_publish_worker,
)
from app.facebook_connections import (
    ensure_user_zernio_profile,
    sync_user_facebook_connections,
)
from app.facebook_publish_queue import (
    FacebookPublishQueueError,
    queue_facebook_publish_job,
)
from app.zernio_client import (
    ZernioError,
    disconnect_account,
    get_facebook_connect_url,
)
from app.api_settings import (
    has_api_key,
    remove_api_key,
    save_api_key,
)
from app.audio_export import create_narration_mp3
from app.security import hash_password, verify_password
from app.voice_registry import (
    DEFAULT_NARRATOR_VOICE,
    validate_voice_speed, validated_voice_pool, voice_template_context,
)

from app.zimage_debug_routes import router as zimage_debug_router

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_queue_worker()
    start_youtube_publish_worker()
    start_facebook_publish_worker()
    yield

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(zimage_debug_router)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_days * 86400,
    https_only=settings.app_url.lower().startswith("https://"),
    same_site="lax",
)

app.mount("/static", StaticFiles(directory="backend/app/static"), name="static")
templates = Jinja2Templates(directory="backend/app/templates")
templates.env.globals.update(voice_template_context())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/dashboard/metrics")
def dashboard_metrics(request: Request):
    if not request.session.get("user_id"):
        return {"authenticated": False}

    return {
        "authenticated": True,
        **collect_dashboard_metrics(),
    }


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



def _normalize_recovery_code(value: str) -> str:
    return (
        value.strip()
        .upper()
        .replace("-", "")
        .replace(" ", "")
    )


def _recovery_code_digest(value: str) -> str:
    import hashlib

    normalized = _normalize_recovery_code(value)

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


@app.get("/forgot-password")
def forgot_password_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(
            "/profile",
            status_code=303,
        )

    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={
            "error": None,
            "success": None,
        },
    )


@app.post("/forgot-password")
def forgot_password(
    request: Request,
    identifier: str = Form(...),
    recovery_code: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    import hmac

    identifier = identifier.strip()

    error = None

    if len(new_password) < 8:
        error = "New password must be at least 8 characters."

    elif new_password != confirm_password:
        error = "New passwords do not match."

    user = None

    if error is None:
        user = db.scalar(
            select(User).where(
                User.username == identifier
            )
        )

        if user is None:
            user = db.scalar(
                select(User).where(
                    User.email == identifier
                )
            )

        supplied_hash = _recovery_code_digest(
            recovery_code
        )

        valid_code = bool(
            user
            and user.is_active
            and user.recovery_code_hash
            and hmac.compare_digest(
                user.recovery_code_hash,
                supplied_hash,
            )
        )

        if not valid_code:
            error = (
                "The account or recovery code is invalid."
            )

    if error:
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={
                "error": error,
                "success": None,
            },
            status_code=400,
        )

    user.password_hash = hash_password(new_password)

    # Recovery codes are one-time use.
    user.recovery_code_hash = None
    user.recovery_code_created_at = None

    db.commit()

    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={
            "error": None,
            "success": (
                "Password reset successfully. "
                "You can now sign in."
            ),
        },
    )


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/profile")
def profile_page(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    user = db.get(
        User,
        int(request.session["user_id"]),
    )

    if user is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "app_name": settings.app_name,
            "active_page": "profile",
            "project_info": get_project_info(),
            "user": user,
            "error": (
                "Current password is incorrect."
                if request.query_params.get("recovery")
                == "invalid_password"
                else None
            ),
            "success": (
                "Profile updated successfully."
                if request.query_params.get("saved") == "1"
                else None
            ),
        },
    )


@app.post("/profile")
def update_profile(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    user = db.get(
        User,
        int(request.session["user_id"]),
    )

    if user is None:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    username = username.strip()
    email = email.strip()

    error = None

    if len(username) < 3 or len(username) > 100:
        error = "Username must be between 3 and 100 characters."

    elif not email or "@" not in email or len(email) > 255:
        error = "Enter a valid email address."

    else:
        existing_username = db.scalar(
            select(User).where(
                User.username == username,
                User.id != user.id,
            )
        )

        existing_email = db.scalar(
            select(User).where(
                User.email == email,
                User.id != user.id,
            )
        )

        if existing_username is not None:
            error = "That username is already in use."

        elif existing_email is not None:
            error = "That email address is already in use."

    changing_password = bool(
        current_password
        or new_password
        or confirm_password
    )

    if error is None and changing_password:
        if not current_password:
            error = "Enter your current password."

        elif not verify_password(
            current_password,
            user.password_hash,
        ):
            error = "Current password is incorrect."

        elif len(new_password) < 8:
            error = "New password must be at least 8 characters."

        elif new_password != confirm_password:
            error = "New password confirmation does not match."

    if error is not None:
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={
                "app_name": settings.app_name,
                "active_page": "profile",
                "project_info": get_project_info(),
                "user": user,
                "error": error,
                "success": None,
                "form_username": username,
                "form_email": email,
            },
            status_code=400,
        )

    user.username = username
    user.email = email

    if changing_password:
        user.password_hash = hash_password(
            new_password
        )

    db.commit()

    return RedirectResponse(
        "/profile?saved=1",
        status_code=303,
    )


JOB_STATUS_LABELS = {
    "waiting": "Waiting",
    "processing": "Starting",
    "outline_generation": "Outline Generation",
    "script_sections": "Script Section Generation",
    "script_validation": "Script Validation",
    "script_refinement": "Script Repair / Refinement",
    "scene_splitting": "Scene Splitting",
    "scene_enrichment": "Scene Metadata Generation",
    "scene_planning": "Scene Planning",
    "narration_tts": "Narration / TTS",
    "media_collection": "Media Collection",
    "captions": "Captions",
    "final_rendering": "Final Rendering",
    "script_generation": "Script Generation",
    "generating_script": "Script Generation",
    "narration": "Narration / TTS",
    "generating_audio": "Narration / TTS",
    "media": "Media Collection",
    "generating_media": "Media Collection",
    "generating_captions": "Captions",
    "rendering": "Final Rendering",
    "rendering_video": "Final Rendering",
    "uploading": "Uploading",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


def prepare_job_display(job, publish_job=None, facebook_publish_job=None):
    job.pipeline_status = job.status
    job.pipeline_css_status = job.status
    job.pipeline_progress = job.progress
    job.youtube_url = None
    job.facebook_url = None
    job.facebook_publish_status = None
    job.facebook_publish_progress = 0

    if publish_job is not None:
        job.youtube_url = publish_job.youtube_url

    if facebook_publish_job is not None:
        job.facebook_url = facebook_publish_job.facebook_url
        job.facebook_publish_status = facebook_publish_job.status
        job.facebook_publish_progress = (
            facebook_publish_job.progress or 0
        )

    if (
        job.status == "completed"
        and (
            job.auto_publish_enabled
            or publish_job is not None
        )
    ):
        job.pipeline_status = "youtube_preparing"
        job.pipeline_css_status = "youtube_preparing"
        job.pipeline_progress = 92
        job.display_status = (
            "Preparing YouTube Metadata & Thumbnail Prompt"
        )
        job.eta_text = "YouTube publishing in progress"

        if publish_job is None:
            return job

        job.youtube_url = publish_job.youtube_url
        status = publish_job.status

        if status == "draft":
            job.pipeline_status = "youtube_preparing"
            job.pipeline_progress = 95
            job.display_status = "Ready for YouTube Upload"

        elif status == "queued":
            job.pipeline_status = "youtube_queued"
            job.pipeline_progress = 96
            job.display_status = "Queued for YouTube"

        elif status == "uploading":
            job.pipeline_status = "youtube_uploading"
            job.pipeline_progress = max(
                1,
                min(99, publish_job.progress),
            )
            job.display_status = (
                f"Uploading to YouTube — "
                f"{publish_job.progress}%"
            )

        elif status == "uploaded":
            job.pipeline_progress = 100
            job.pipeline_css_status = "completed"

            if publish_job.scheduled_at is not None:
                job.pipeline_status = "youtube_scheduled"
                job.display_status = "Scheduled"
                job.eta_text = "Scheduled on YouTube"
            else:
                job.pipeline_status = "published"
                job.display_status = "Published"
                job.eta_text = "Published to YouTube"

        elif status == "failed":
            job.pipeline_status = "youtube_failed"
            job.pipeline_css_status = "failed"
            job.pipeline_progress = publish_job.progress
            job.display_status = "YouTube Upload Failed"
            job.eta_text = "Video remains downloadable"

        elif status == "needs_review":
            job.pipeline_status = "youtube_review"
            job.pipeline_css_status = "failed"
            job.pipeline_progress = publish_job.progress
            job.display_status = "YouTube Upload Needs Review"
            job.eta_text = "Manual review required"

        return job

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


def load_dashboard_jobs(db):
    jobs = db.scalars(
        select(VideoJob).order_by(VideoJob.queue_position)
    ).all()

    publish_jobs = db.scalars(
        select(YouTubePublishJob).order_by(
            YouTubePublishJob.id.desc()
        )
    ).all()

    facebook_publish_jobs = db.scalars(
        select(FacebookPublishJob).order_by(
            FacebookPublishJob.id.desc()
        )
    ).all()

    latest_publish_by_video = {}
    latest_facebook_publish_by_video = {}

    for publish_job in publish_jobs:
        latest_publish_by_video.setdefault(
            publish_job.video_job_id,
            publish_job,
        )

    for publish_job in facebook_publish_jobs:
        latest_facebook_publish_by_video.setdefault(
            publish_job.video_job_id,
            publish_job,
        )

    return [
        prepare_job_display(
            job,
            latest_publish_by_video.get(job.id),
            latest_facebook_publish_by_video.get(job.id),
        )
        for job in jobs
    ]


def dashboard_context(request, jobs, active_page):
    return {
        **voice_template_context(),
        "app_name": settings.app_name,
        "jobs": jobs,
        "active_page": active_page,
        "project_info": get_project_info(),
        "system_metrics": collect_dashboard_metrics(),
        "error": None,
        "success": request.query_params.get("success"),
    }


@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    jobs = load_dashboard_jobs(db)

    overview_stats = {
        "total": len(jobs),
        "active": sum(
            job.status not in {
                "waiting",
                "completed",
                "failed",
                "cancelled",
            }
            for job in jobs
        ),
        "completed": sum(
            job.status == "completed"
            for job in jobs
        ),
        "failed": sum(
            job.status == "failed"
            for job in jobs
        ),
    }

    recent_jobs = sorted(
        jobs,
        key=lambda job: job.id,
        reverse=True,
    )[:5]

    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={
            "app_name": settings.app_name,
            "active_page": "overview",
            "project_info": get_project_info(),
            "overview_stats": overview_stats,
            "recent_jobs": recent_jobs,
            "system_metrics": collect_dashboard_metrics(),
        },
    )



def _title_research_page_data(
    db: Session,
    user_id: int,
):
    profiles = db.scalars(
        select(TitleResearchProfile)
        .where(
            TitleResearchProfile.user_id == user_id
        )
        .order_by(
            TitleResearchProfile.name.asc()
        )
    ).all()

    youtube_connections = db.scalars(
        select(YouTubeConnection)
        .where(
            YouTubeConnection.is_active.is_(True),
            YouTubeConnection.encrypted_refresh_token.is_not(None),
        )
        .order_by(
            YouTubeConnection.channel_title.asc()
        )
    ).all()

    profile_payload = [
        {
            "id": profile.id,
            "name": profile.name,
            "youtube_connection_id": (
                profile.youtube_connection_id or 0
            ),
            "prompt": profile.prompt or "",
            "niche": profile.niche or "",
            "competitor": profile.competitor or "",
        }
        for profile in profiles
    ]

    return (
        profiles,
        youtube_connections,
        profile_payload,
    )


@app.get("/title-research")
def title_research_page(
    request: Request,
    success: str = "",
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    user_id = int(request.session["user_id"])

    (
        profiles,
        youtube_connections,
        profile_payload,
    ) = _title_research_page_data(
        db,
        user_id,
    )

    default_connection_id = (
        youtube_connections[0].id
        if youtube_connections
        else 0
    )

    return templates.TemplateResponse(
        request=request,
        name="title_research.html",
        context={
            "app_name": settings.app_name,
            "active_page": "title_research",
            "project_info": get_project_info(),
            "research": None,
            "error": None,
            "success": success,
            "saved_profiles": profiles,
            "saved_profile_payload": profile_payload,
            "youtube_connections": youtube_connections,
            "form_values": {
                "profile_id": 0,
                "profile_name": "",
                "youtube_connection_id": default_connection_id,
                "prompt": "",
                "niche": "",
                "competitor": "",
            },
        },
    )


@app.post("/title-research")
def run_title_research(
    request: Request,
    profile_id: int = Form(0),
    profile_name: str = Form(""),
    youtube_connection_id: int = Form(0),
    prompt: str = Form(""),
    niche: str = Form(""),
    competitor: str = Form(""),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    user_id = int(request.session["user_id"])

    research = None
    error = None

    try:
        research = research_titles(
            db=db,
            prompt=prompt,
            niche=niche,
            competitor=competitor,
            youtube_connection_id=(
                youtube_connection_id or None
            ),
        )
    except Exception as exc:
        logger.exception("Title research failed")
        error = str(exc)

    (
        profiles,
        youtube_connections,
        profile_payload,
    ) = _title_research_page_data(
        db,
        user_id,
    )

    return templates.TemplateResponse(
        request=request,
        name="title_research.html",
        context={
            "app_name": settings.app_name,
            "active_page": "title_research",
            "project_info": get_project_info(),
            "research": research,
            "error": error,
            "success": "",
            "saved_profiles": profiles,
            "saved_profile_payload": profile_payload,
            "youtube_connections": youtube_connections,
            "form_values": {
                "profile_id": profile_id,
                "profile_name": profile_name,
                "youtube_connection_id": youtube_connection_id,
                "prompt": prompt,
                "niche": niche,
                "competitor": competitor,
            },
        },
    )


@app.post("/title-research/profiles/save")
def save_title_research_profile(
    request: Request,
    profile_id: int = Form(0),
    profile_name: str = Form(""),
    youtube_connection_id: int = Form(0),
    prompt: str = Form(""),
    niche: str = Form(""),
    competitor: str = Form(""),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    user_id = int(request.session["user_id"])
    profile_name = profile_name.strip()

    if not profile_name:
        return RedirectResponse(
            "/title-research?success=Enter a profile name before saving.",
            status_code=303,
        )

    connection = db.get(
        YouTubeConnection,
        youtube_connection_id,
    )

    if (
        connection is None
        or not connection.is_active
        or not connection.encrypted_refresh_token
    ):
        return RedirectResponse(
            "/title-research?success=Select an active YouTube channel.",
            status_code=303,
        )

    profile = None

    if profile_id:
        profile = db.scalar(
            select(TitleResearchProfile)
            .where(
                TitleResearchProfile.id == profile_id,
                TitleResearchProfile.user_id == user_id,
            )
        )

    if profile is None:
        profile = TitleResearchProfile(
            user_id=user_id,
            name=profile_name,
        )
        db.add(profile)

    profile.name = profile_name
    profile.youtube_connection_id = (
        youtube_connection_id
    )
    profile.prompt = prompt.strip()
    profile.niche = niche.strip()
    profile.competitor = competitor.strip()

    db.commit()

    return RedirectResponse(
        "/title-research?success=Research profile saved.",
        status_code=303,
    )


@app.post("/title-research/profiles/delete")
def delete_title_research_profile(
    request: Request,
    profile_id: int = Form(0),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    user_id = int(request.session["user_id"])

    profile = db.scalar(
        select(TitleResearchProfile)
        .where(
            TitleResearchProfile.id == profile_id,
            TitleResearchProfile.user_id == user_id,
        )
    )

    if profile is not None:
        db.delete(profile)
        db.commit()

    return RedirectResponse(
        "/title-research?success=Research profile deleted.",
        status_code=303,
    )


@app.get("/avatars")
def avatars_page(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    avatars = db.scalars(
        select(Avatar)
        .where(
            Avatar.user_id == int(
                request.session["user_id"]
            )
        )
        .order_by(Avatar.created_at.desc())
    ).all()

    context = dashboard_context(
        request,
        load_dashboard_jobs(db),
        "avatars",
    )
    context["avatars"] = avatars

    return templates.TemplateResponse(
        request=request,
        name="avatars.html",
        context=context,
    )


def _prepare_avatar_background(avatar_id, cache_id, source_path):
    db = SessionLocal()
    try:
        avatar = db.get(Avatar, int(avatar_id))
        if avatar is None:
            return
        try:
            prepare_avatar(cache_id=cache_id, source_path=Path(source_path))
            avatar.status = "ready"
            avatar.error_message = None
            db.commit()
        except Exception as error:
            avatar.status = "failed"
            avatar.error_message = str(error)[-6000:]
            db.commit()
            from app.avatar_service import avatar_cache_path
            cache_path = avatar_cache_path(cache_id)
            if cache_path.exists():
                shutil.rmtree(cache_path, ignore_errors=True)
    finally:
        db.close()


@app.post("/avatars")
def create_avatar(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    source_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse("/login", status_code=303)

    name = name.strip()

    if not name:
        return RedirectResponse(
            "/avatars?success=Avatar name is required.",
            status_code=303,
        )

    original_name = (
        source_file.filename or ""
    ).strip()

    suffix = Path(original_name).suffix.lower()

    try:
        source_type = source_type_for_path(
            Path(f"avatar{suffix}")
        )
    except ValueError as error:
        return RedirectResponse(
            f"/avatars?success={str(error)}",
            status_code=303,
        )

    ensure_avatar_storage()

    cache_id = create_cache_id(user_id)

    source_path = (
        AVATAR_STORAGE_ROOT
        / f"{cache_id}{suffix}"
    )

    avatar = Avatar(
        user_id=int(user_id),
        name=name[:255],
        source_path=str(source_path),
        source_type=source_type,
        cache_id=cache_id,
        status="preparing",
    )

    db.add(avatar)
    db.commit()
    db.refresh(avatar)

    try:
        with source_path.open("wb") as destination:
            shutil.copyfileobj(
                source_file.file,
                destination,
            )

        if source_path.stat().st_size <= 0:
            raise RuntimeError(
                "Uploaded avatar file is empty."
            )

        background_tasks.add_task(
            _prepare_avatar_background,
            avatar.id,
            cache_id,
            str(source_path),
        )

        return RedirectResponse(
            "/avatars?success=Avatar uploaded. Preparation is running.",
            status_code=303,
        )

    except Exception as error:
        avatar.status = "failed"
        avatar.error_message = str(error)[-6000:]
        db.commit()

        # Preserve the uploaded source file so failed avatars
        # can be inspected and diagnosed.
        from app.avatar_service import avatar_cache_path

        cache_path = avatar_cache_path(cache_id)
        if cache_path.exists():
            shutil.rmtree(
                cache_path,
                ignore_errors=True,
            )

        return RedirectResponse(
            "/avatars?success=Avatar preparation failed. Check the source image/video and try again.",
            status_code=303,
        )

    finally:
        try:
            source_file.file.close()
        except Exception:
            pass


@app.get("/avatars/{avatar_id}/source")
def avatar_source(
    avatar_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse("/login", status_code=303)

    avatar = db.scalar(
        select(Avatar).where(
            Avatar.id == avatar_id,
            Avatar.user_id == int(user_id),
        )
    )

    if avatar is None:
        return RedirectResponse(
            "/avatars",
            status_code=303,
        )

    source_path = Path(
        avatar.source_path
    ).resolve()

    try:
        source_path.relative_to(
            AVATAR_STORAGE_ROOT.resolve()
        )
    except ValueError:
        return RedirectResponse(
            "/avatars",
            status_code=303,
        )

    if not source_path.is_file():
        return RedirectResponse(
            "/avatars",
            status_code=303,
        )

    return FileResponse(source_path)


@app.post("/avatars/{avatar_id}/delete")
def delete_avatar(
    avatar_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse("/login", status_code=303)

    avatar = db.scalar(
        select(Avatar).where(
            Avatar.id == avatar_id,
            Avatar.user_id == int(user_id),
        )
    )

    if avatar is not None:
        delete_avatar_files(
            avatar.cache_id,
            avatar.source_path,
        )
        db.delete(avatar)
        db.commit()

    return RedirectResponse(
        "/avatars?success=Avatar deleted.",
        status_code=303,
    )


@app.get("/videos/create")
def create_video_page(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    jobs = load_dashboard_jobs(db)

    user_id = int(request.session["user_id"])

    facebook_connections = db.scalars(
        select(FacebookConnection)
        .where(
            FacebookConnection.user_id == user_id,
        )
        .order_by(
            FacebookConnection.connected_at.desc()
        )
    ).all()

    youtube_connections = db.scalars(
        select(YouTubeConnection)
        .where(
            YouTubeConnection.is_active.is_(True),
            YouTubeConnection.encrypted_refresh_token.is_not(None),
        )
        .order_by(YouTubeConnection.channel_title)
    ).all()

    context = dashboard_context(
        request,
        jobs,
        "create",
    )
    context["youtube_connections"] = youtube_connections

    user_id = int(request.session["user_id"])
    facebook_connections = db.scalars(
        select(FacebookConnection)
        .where(
            FacebookConnection.user_id == user_id,
            FacebookConnection.is_active.is_(True),
        )
        .order_by(FacebookConnection.display_name)
    ).all()
    context["facebook_connections"] = facebook_connections

    avatars = db.scalars(
        select(Avatar)
        .where(
            Avatar.user_id == int(request.session["user_id"]),
            Avatar.status == "ready",
        )
        .order_by(Avatar.name)
    ).all()
    context["avatars"] = avatars

    return templates.TemplateResponse(
        request=request,
        name="create_video.html",
        context=context,
    )


@app.get("/script-to-video")
def script_to_video_page(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    jobs = load_dashboard_jobs(db)

    youtube_connections = db.scalars(
        select(YouTubeConnection)
        .where(
            YouTubeConnection.is_active.is_(True),
            YouTubeConnection.encrypted_refresh_token.is_not(None),
        )
        .order_by(YouTubeConnection.channel_title)
    ).all()

    context = dashboard_context(
        request,
        jobs,
        "script_to_video",
    )
    context["youtube_connections"] = youtube_connections

    user_id = int(request.session["user_id"])
    facebook_connections = db.scalars(
        select(FacebookConnection)
        .where(
            FacebookConnection.user_id == user_id,
            FacebookConnection.is_active.is_(True),
        )
        .order_by(FacebookConnection.display_name)
    ).all()
    context["facebook_connections"] = facebook_connections

    avatars = db.scalars(
        select(Avatar)
        .where(
            Avatar.user_id == int(request.session["user_id"]),
            Avatar.status == "ready",
        )
        .order_by(Avatar.name)
    ).all()
    context["avatars"] = avatars
    context["script_mode"] = True

    return templates.TemplateResponse(
        request=request,
        name="script_to_video.html",

        context=context,
    )


@app.get("/videos")
def videos_page(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    jobs = load_dashboard_jobs(db)

    return templates.TemplateResponse(
        request=request,
        name="videos.html",
        context=dashboard_context(
            request,
            jobs,
            "videos",
        ),
    )


from app.video_profiles import normalize_profile_key


@app.post("/jobs")
def add_jobs(
    request: Request,
    titles: str = Form(...),
    duration_minutes: int = Form(10),
    video_style: str = Form("auto"),
    orientation: str = Form("landscape"),
    media_source: str = Form("stock_only"),
    narrator_voice: str = Form(DEFAULT_NARRATOR_VOICE),
    voice_pool: list[str] = Form(default=[]),
    voice_speed: float = Form(1.0),
    captions_enabled: str = Form("enabled"),
    caption_font_family: str = Form("Manrope"),
    caption_font_color: str = Form("#FFFFFF"),
    caption_font_size: int = Form(48),
    caption_position: str = Form("bottom"),
    caption_background: str = Form("on"),
    caption_outline: int = Form(2),
    avatar_id: int = Form(0),
    avatar_position: str = Form("bottom_right"),
    auto_publish_connection_id: int = Form(0),
    auto_publish_privacy: str = Form("private"),
    auto_publish_scheduled_at_local: str = Form(""),
    auto_publish_scheduled_at_utc: str = Form(""),
    auto_publish_made_for_kids: str = Form("no"),
    auto_publish_notify_subscribers: str = Form(""),
    auto_publish_metadata: str = Form("on"),
    facebook_auto_publish_connection_id: int = Form(0),
    facebook_auto_publish_mode: str = Form("now"),
    facebook_auto_publish_scheduled_at_local: str = Form(""),
    facebook_auto_publish_scheduled_at_utc: str = Form(""),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    # Validate the requested production profile.
    video_style = normalize_profile_key(video_style)

    title_list = [title.strip() for title in titles.splitlines() if title.strip()]
    try:
        selected_voices = validated_voice_pool([narrator_voice, *voice_pool])
        narrator_voice = selected_voices[0]
        voice_speed = validate_voice_speed(voice_speed)
    except ValueError:
        return RedirectResponse(
            "/videos/create?success=Invalid narrator voice or speed.",
            status_code=303,
        )

    captions_enabled_value = captions_enabled == "enabled"
    caption_background_value = caption_background == "on"

    allowed_caption_fonts = {
        "Manrope",
        "Inter",
        "Montserrat",
        "Poppins",
        "Roboto",
        "Open Sans",
        "Lato",
        "Nunito",
        "Merriweather",
        "Source Sans 3",
    }

    if caption_font_family not in allowed_caption_fonts:
        caption_font_family = "Manrope"

    caption_font_color = str(caption_font_color or "").strip()

    if not (
        len(caption_font_color) == 7
        and caption_font_color.startswith("#")
        and all(
            c in "0123456789abcdefABCDEF"
            for c in caption_font_color[1:]
        )
    ):
        caption_font_color = "#FFFFFF"

    caption_font_color = caption_font_color.upper()

    # AI scene media is currently disabled.
    media_source = "stock_only"

    if caption_font_size not in {36, 48, 60}:
        caption_font_size = 48

    if caption_position not in {"top", "middle", "bottom"}:
        caption_position = "bottom"

    if caption_outline not in {0, 1, 2, 4}:
        caption_outline = 2

    if avatar_position not in {
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    }:
        avatar_position = "bottom_right"

    selected_avatar = None

    if avatar_id > 0:
        selected_avatar = db.scalar(
            select(Avatar).where(
                Avatar.id == avatar_id,
                Avatar.user_id == int(
                    request.session["user_id"]
                ),
                Avatar.status == "ready",
            )
        )

        if selected_avatar is None:
            return RedirectResponse(
                "/videos/create?success=Selected avatar is unavailable.",
                status_code=303,
            )

    auto_publish_enabled = auto_publish_connection_id > 0
    selected_youtube_connection = None

    if auto_publish_enabled:
        selected_youtube_connection = db.get(
            YouTubeConnection,
            auto_publish_connection_id,
        )

        if (
            selected_youtube_connection is None
            or not selected_youtube_connection.is_active
            or not selected_youtube_connection.encrypted_refresh_token
        ):
            return RedirectResponse(
                "/videos/create?success=Selected YouTube channel is unavailable.",
                status_code=303,
            )

    publish_mode = auto_publish_privacy.strip().lower()

    if publish_mode not in {
        "private",
        "unlisted",
        "public",
        "scheduled",
    }:
        publish_mode = "private"

    auto_publish_scheduled_at = None

    if auto_publish_enabled and publish_mode == "scheduled":
        if not auto_publish_scheduled_at_utc.strip():
            return RedirectResponse(
                "/videos/create?success=Choose a future date and time for scheduled publishing.",
                status_code=303,
            )

        try:
            parsed_schedule = datetime.fromisoformat(
                auto_publish_scheduled_at_utc.strip().replace(
                    "Z",
                    "+00:00",
                )
            )

            if parsed_schedule.tzinfo is None:
                parsed_schedule = parsed_schedule.replace(
                    tzinfo=timezone.utc
                )

            parsed_schedule = parsed_schedule.astimezone(
                timezone.utc
            )

            if parsed_schedule <= datetime.now(timezone.utc):
                return RedirectResponse(
                    "/videos/create?success=Scheduled publishing time must be in the future.",
                    status_code=303,
                )

            auto_publish_scheduled_at = (
                parsed_schedule.replace(tzinfo=None)
            )

        except ValueError:
            return RedirectResponse(
                "/videos/create?success=Invalid scheduled publishing date and time.",
                status_code=303,
            )

        # YouTube scheduling uses private + publishAt internally.
        auto_publish_privacy = "private"

    elif publish_mode == "scheduled":
        auto_publish_privacy = "private"

    else:
        auto_publish_privacy = publish_mode

    made_for_kids_value = (
        auto_publish_made_for_kids == "yes"
    )
    notify_subscribers_value = (
        auto_publish_notify_subscribers == "on"
    )
    auto_metadata_value = auto_publish_metadata == "on"

    facebook_auto_publish_enabled = (
        facebook_auto_publish_connection_id > 0
    )
    selected_facebook_connection = None
    facebook_auto_publish_scheduled_at = None

    if facebook_auto_publish_enabled:
        selected_facebook_connection = db.scalar(
            select(FacebookConnection).where(
                FacebookConnection.id
                == facebook_auto_publish_connection_id,
                FacebookConnection.user_id
                == int(request.session["user_id"]),
                FacebookConnection.is_active.is_(True),
            )
        )

        if selected_facebook_connection is None:
            return RedirectResponse(
                "/videos/create?success=Selected Facebook Page is unavailable.",
                status_code=303,
            )

        facebook_publish_mode = (
            facebook_auto_publish_mode
            .strip()
            .lower()
        )

        if facebook_publish_mode not in {
            "now",
            "scheduled",
        }:
            facebook_publish_mode = "now"

        if facebook_publish_mode == "scheduled":
            if not facebook_auto_publish_scheduled_at_utc.strip():
                return RedirectResponse(
                    "/videos/create?success=Choose a future Facebook publishing date and time.",
                    status_code=303,
                )

            try:
                parsed_facebook_schedule = datetime.fromisoformat(
                    facebook_auto_publish_scheduled_at_utc
                    .strip()
                    .replace("Z", "+00:00")
                )

                if parsed_facebook_schedule.tzinfo is None:
                    parsed_facebook_schedule = (
                        parsed_facebook_schedule.replace(
                            tzinfo=timezone.utc
                        )
                    )

                parsed_facebook_schedule = (
                    parsed_facebook_schedule.astimezone(
                        timezone.utc
                    )
                )

                if (
                    parsed_facebook_schedule
                    <= datetime.now(timezone.utc)
                ):
                    return RedirectResponse(
                        "/videos/create?success=Facebook publishing time must be in the future.",
                        status_code=303,
                    )

                facebook_auto_publish_scheduled_at = (
                    parsed_facebook_schedule.replace(
                        tzinfo=None
                    )
                )

            except ValueError:
                return RedirectResponse(
                    "/videos/create?success=Invalid Facebook publishing date and time.",
                    status_code=303,
                )

    if not title_list:
        return RedirectResponse("/videos/create?success=No valid titles entered.", status_code=303)
    if len(title_list) > 20:
        return RedirectResponse("/videos/create?success=Maximum 20 titles allowed.", status_code=303)

    # Remove legacy orphan YouTube publish records before
    # a deleted VideoJob ID can ever be reused by SQLite.
    db.execute(
        delete(YouTubePublishJob).where(
            ~YouTubePublishJob.video_job_id.in_(
                select(VideoJob.id)
            )
        )
    )

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
            caption_font_family=caption_font_family,
            caption_font_color=caption_font_color,
            caption_font_size=caption_font_size,
            caption_position=caption_position,
            caption_background=caption_background_value,
            caption_outline=caption_outline,
            avatar_id=(
                selected_avatar.id
                if selected_avatar
                else None
            ),
            avatar_position=avatar_position,
            auto_publish_enabled=auto_publish_enabled,
            auto_publish_connection_id=(
                selected_youtube_connection.id
                if selected_youtube_connection
                else None
            ),
            auto_publish_privacy=auto_publish_privacy,
            auto_publish_scheduled_at=auto_publish_scheduled_at,
            auto_publish_made_for_kids=made_for_kids_value,
            auto_publish_notify_subscribers=(
                notify_subscribers_value
            ),
            auto_publish_metadata=auto_metadata_value,
            auto_publish_thumbnail=False,
            facebook_auto_publish_enabled=(
                facebook_auto_publish_enabled
            ),
            facebook_auto_publish_connection_id=(
                selected_facebook_connection.id
                if selected_facebook_connection
                else None
            ),
            facebook_auto_publish_scheduled_at=(
                facebook_auto_publish_scheduled_at
            ),
            status="waiting",
            progress=0,
            queue_position=next_position + index,
        ))

    db.commit()
    start_queue_worker()
    return RedirectResponse(
        f"/videos?success={len(title_list)} title(s) added to queue.",
        status_code=303,
    )


@app.post("/jobs/script")
def add_script_job(
    request: Request,
    title: str = Form(...),
    source_script: str = Form(...),
    video_style: str = Form("auto"),
    orientation: str = Form("landscape"),
    media_source: str = Form("stock_only"),
    narrator_voice: str = Form(DEFAULT_NARRATOR_VOICE),
    voice_pool: list[str] = Form(default=[]),
    voice_speed: float = Form(1.0),
    captions_enabled: str = Form("enabled"),
    caption_font_family: str = Form("Manrope"),
    caption_font_color: str = Form("#FFFFFF"),
    caption_font_size: int = Form(48),
    caption_position: str = Form("bottom"),
    caption_background: str = Form("on"),
    caption_outline: int = Form(2),
    avatar_id: int = Form(0),
    avatar_position: str = Form("bottom_right"),
    auto_publish_connection_id: int = Form(0),
    auto_publish_privacy: str = Form("private"),
    auto_publish_scheduled_at_local: str = Form(""),
    auto_publish_scheduled_at_utc: str = Form(""),
    auto_publish_made_for_kids: str = Form("no"),
    auto_publish_notify_subscribers: str = Form(""),
    auto_publish_metadata: str = Form("on"),
    facebook_auto_publish_connection_id: int = Form(0),
    facebook_auto_publish_mode: str = Form("now"),
    facebook_auto_publish_scheduled_at_local: str = Form(""),
    facebook_auto_publish_scheduled_at_utc: str = Form(""),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    title = title.strip()
    source_script = source_script.strip()

    if not title:
        return RedirectResponse(
            "/script-to-video?success=Video title is required.",
            status_code=303,
        )

    if len(title) > 500:
        return RedirectResponse(
            "/script-to-video?success=Video title is too long.",
            status_code=303,
        )

    word_count = len(source_script.split())

    if word_count == 0:
        return RedirectResponse(
            "/script-to-video?success=Paste a script first.",
            status_code=303,
        )

    # Approximate narration duration at the project's 145 WPM baseline.
    # Round to the nearest whole minute because VideoJob currently stores
    # duration as an integer.
    duration_minutes = max(
        1,
        (word_count + 72) // 145,
    )

    video_style = normalize_profile_key(video_style)

    try:
        selected_voices = validated_voice_pool(
            [narrator_voice, *voice_pool]
        )
        narrator_voice = selected_voices[0]
        voice_speed = validate_voice_speed(voice_speed)
    except ValueError:
        return RedirectResponse(
            "/script-to-video?success=Invalid narrator voice or speed.",
            status_code=303,
        )

    captions_enabled_value = captions_enabled == "enabled"
    caption_background_value = caption_background == "on"

    allowed_caption_fonts = {
        "Manrope",
        "Inter",
        "Montserrat",
        "Poppins",
        "Roboto",
        "Open Sans",
        "Lato",
        "Nunito",
        "Merriweather",
        "Source Sans 3",
    }

    if caption_font_family not in allowed_caption_fonts:
        caption_font_family = "Manrope"

    caption_font_color = str(caption_font_color or "").strip()

    if not (
        len(caption_font_color) == 7
        and caption_font_color.startswith("#")
        and all(
            c in "0123456789abcdefABCDEF"
            for c in caption_font_color[1:]
        )
    ):
        caption_font_color = "#FFFFFF"

    caption_font_color = caption_font_color.upper()

    # AI scene media is intentionally disabled.
    media_source = "stock_only"

    if caption_font_size not in {36, 48, 60}:
        caption_font_size = 48

    if caption_position not in {"top", "middle", "bottom"}:
        caption_position = "bottom"

    if caption_outline not in {0, 1, 2, 4}:
        caption_outline = 2

    if avatar_position not in {
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    }:
        avatar_position = "bottom_right"

    selected_avatar = None

    if avatar_id > 0:
        selected_avatar = db.scalar(
            select(Avatar).where(
                Avatar.id == avatar_id,
                Avatar.user_id == int(
                    request.session["user_id"]
                ),
                Avatar.status == "ready",
            )
        )

        if selected_avatar is None:
            return RedirectResponse(
                "/script-to-video?success=Selected avatar is unavailable.",
                status_code=303,
            )

    auto_publish_enabled = auto_publish_connection_id > 0
    selected_youtube_connection = None

    if auto_publish_enabled:
        selected_youtube_connection = db.get(
            YouTubeConnection,
            auto_publish_connection_id,
        )

        if (
            selected_youtube_connection is None
            or not selected_youtube_connection.is_active
            or not selected_youtube_connection.encrypted_refresh_token
        ):
            return RedirectResponse(
                "/script-to-video?success=Selected YouTube channel is unavailable.",
                status_code=303,
            )

    publish_mode = auto_publish_privacy.strip().lower()

    if publish_mode not in {
        "private",
        "unlisted",
        "public",
        "scheduled",
    }:
        publish_mode = "private"

    auto_publish_scheduled_at = None

    if auto_publish_enabled and publish_mode == "scheduled":
        if not auto_publish_scheduled_at_utc.strip():
            return RedirectResponse(
                "/script-to-video?success=Choose a future date and time for scheduled publishing.",
                status_code=303,
            )

        try:
            parsed_schedule = datetime.fromisoformat(
                auto_publish_scheduled_at_utc.strip().replace(
                    "Z",
                    "+00:00",
                )
            )

            if parsed_schedule.tzinfo is None:
                parsed_schedule = parsed_schedule.replace(
                    tzinfo=timezone.utc
                )

            parsed_schedule = parsed_schedule.astimezone(
                timezone.utc
            )

            if parsed_schedule <= datetime.now(timezone.utc):
                return RedirectResponse(
                    "/script-to-video?success=Scheduled publishing time must be in the future.",
                    status_code=303,
                )

            auto_publish_scheduled_at = (
                parsed_schedule.replace(tzinfo=None)
            )

        except ValueError:
            return RedirectResponse(
                "/script-to-video?success=Invalid scheduled publishing date and time.",
                status_code=303,
            )

        auto_publish_privacy = "private"

    elif publish_mode == "scheduled":
        auto_publish_privacy = "private"

    else:
        auto_publish_privacy = publish_mode

    made_for_kids_value = (
        auto_publish_made_for_kids == "yes"
    )
    notify_subscribers_value = (
        auto_publish_notify_subscribers == "on"
    )
    auto_metadata_value = auto_publish_metadata == "on"

    facebook_auto_publish_enabled = (
        facebook_auto_publish_connection_id > 0
    )
    selected_facebook_connection = None
    facebook_auto_publish_scheduled_at = None

    if facebook_auto_publish_enabled:
        selected_facebook_connection = db.scalar(
            select(FacebookConnection).where(
                FacebookConnection.id
                == facebook_auto_publish_connection_id,
                FacebookConnection.user_id
                == int(request.session["user_id"]),
                FacebookConnection.is_active.is_(True),
            )
        )

        if selected_facebook_connection is None:
            return RedirectResponse(
                "/script-to-video?success=Selected Facebook Page is unavailable.",
                status_code=303,
            )

        facebook_publish_mode = (
            facebook_auto_publish_mode
            .strip()
            .lower()
        )

        if facebook_publish_mode not in {
            "now",
            "scheduled",
        }:
            facebook_publish_mode = "now"

        if facebook_publish_mode == "scheduled":
            if not facebook_auto_publish_scheduled_at_utc.strip():
                return RedirectResponse(
                    "/script-to-video?success=Choose a future Facebook publishing date and time.",
                    status_code=303,
                )

            try:
                parsed_facebook_schedule = datetime.fromisoformat(
                    facebook_auto_publish_scheduled_at_utc
                    .strip()
                    .replace("Z", "+00:00")
                )

                if parsed_facebook_schedule.tzinfo is None:
                    parsed_facebook_schedule = (
                        parsed_facebook_schedule.replace(
                            tzinfo=timezone.utc
                        )
                    )

                parsed_facebook_schedule = (
                    parsed_facebook_schedule.astimezone(
                        timezone.utc
                    )
                )

                if (
                    parsed_facebook_schedule
                    <= datetime.now(timezone.utc)
                ):
                    return RedirectResponse(
                        "/script-to-video?success=Facebook publishing time must be in the future.",
                        status_code=303,
                    )

                facebook_auto_publish_scheduled_at = (
                    parsed_facebook_schedule.replace(
                        tzinfo=None
                    )
                )

            except ValueError:
                return RedirectResponse(
                    "/script-to-video?success=Invalid Facebook publishing date and time.",
                    status_code=303,
                )

    # Same orphan cleanup used by the normal title workflow.
    db.execute(
        delete(YouTubePublishJob).where(
            ~YouTubePublishJob.video_job_id.in_(
                select(VideoJob.id)
            )
        )
    )

    last_job = db.scalar(
        select(VideoJob)
        .order_by(VideoJob.queue_position.desc())
        .limit(1)
    )

    next_position = (
        last_job.queue_position + 1
        if last_job
        else 1
    )

    job = VideoJob(
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
        caption_font_family=caption_font_family,
        caption_font_color=caption_font_color,
        caption_font_size=caption_font_size,
        caption_position=caption_position,
        caption_background=caption_background_value,
        caption_outline=caption_outline,
        avatar_id=(
            selected_avatar.id
            if selected_avatar
            else None
        ),
        avatar_position=avatar_position,

        input_mode="script",
        source_script=source_script,

        auto_publish_enabled=auto_publish_enabled,
        auto_publish_connection_id=(
            selected_youtube_connection.id
            if selected_youtube_connection
            else None
        ),
        auto_publish_privacy=auto_publish_privacy,
        auto_publish_scheduled_at=auto_publish_scheduled_at,
        auto_publish_made_for_kids=made_for_kids_value,
        auto_publish_notify_subscribers=notify_subscribers_value,
        auto_publish_metadata=auto_metadata_value,
        auto_publish_thumbnail=False,
        facebook_auto_publish_enabled=(
            facebook_auto_publish_enabled
        ),
        facebook_auto_publish_connection_id=(
            selected_facebook_connection.id
            if selected_facebook_connection
            else None
        ),
        facebook_auto_publish_scheduled_at=(
            facebook_auto_publish_scheduled_at
        ),

        status="waiting",
        progress=0,
        queue_position=next_position,
    )

    db.add(job)
    db.commit()

    start_queue_worker()

    return RedirectResponse(
        f"/videos?success=Script video added to queue ({word_count} words, ~{duration_minutes} min).",
        status_code=303,
    )


@app.get("/jobs/{job_id}/thumbnail")
def job_thumbnail_workspace(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)

    if job is None:
        return RedirectResponse(
            "/videos?success=Video job not found.",
            status_code=303,
        )

    return templates.TemplateResponse(
        request=request,
        name="thumbnail_workspace.html",
        context={
            "app_name": settings.app_name,
            "active_page": "videos",
            "project_info": get_project_info(),
            "job": job,
        },
    )


@app.post("/jobs/{job_id}/thumbnail-prompt")
def generate_job_thumbnail_prompt(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)

    if job is None:
        return RedirectResponse(
            "/videos?success=Video job not found.",
            status_code=303,
        )

    if job.status != "completed":
        return RedirectResponse(
            "/videos?success=Thumbnail prompt is available after video completion.",
            status_code=303,
        )

    try:
        result = generate_youtube_thumbnail_prompt(job)

        job.thumbnail_headline = result.headline
        job.thumbnail_prompt = result.visual_prompt
        job.thumbnail_prompt_provider = result.provider

        db.commit()
        db.refresh(job)

        return RedirectResponse(
            f"/videos?success=Thumbnail prompt generated for job {job.id}.",
            status_code=303,
        )

    except Exception as error:
        db.rollback()
        logging.exception(
            "Could not generate thumbnail prompt for job %s",
            job_id,
        )

        return RedirectResponse(
            "/videos?success=Thumbnail prompt generation failed.",
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
            "/videos?success=Job not found.",
            status_code=303,
        )

    if job.status in {"completed", "failed", "cancelled"}:
        return RedirectResponse(
            "/videos?success=This job cannot be cancelled.",
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
        "/videos?success=Job cancelled.",
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
            "/videos?success=Job not found.",
            status_code=303,
        )

    if job.status not in {"failed", "cancelled"}:
        return RedirectResponse(
            "/videos?success=Only failed or cancelled jobs can be retried.",
            status_code=303,
        )

    # Smart retry: stop any stale worker but preserve completed artifacts.
    # run_job() validates each saved stage and resumes from the earliest
    # invalid/missing dependency instead of deleting the whole job.
    cleanup_job_files(
        worker_pid=job.worker_pid,
        output_directory=None,
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
    # Preserve output_directory/script/scene_plan for stage validation
    # and selective resume.
    db.commit()

    start_queue_worker()

    return RedirectResponse(
        "/videos?success=Job added back to the queue.",
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
            "/videos?success=Job not found.",
            status_code=303,
        )

    cleanup_job_files(
        worker_pid=job.worker_pid,
        output_directory=job.output_directory,
    )

    db.execute(
        delete(YouTubePublishJob).where(
            YouTubePublishJob.video_job_id == job.id
        )
    )
    db.delete(job)
    db.commit()

    return RedirectResponse(
        "/videos?success=Job and all related files deleted.",
        status_code=303,
    )



@app.post("/jobs/bulk-delete")
def bulk_delete_jobs(
    request: Request,
    job_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    selected_ids = list(dict.fromkeys(job_ids))

    if not selected_ids:
        return RedirectResponse(
            "/videos?success=No videos selected.",
            status_code=303,
        )

    jobs = list(
        db.scalars(
            select(VideoJob).where(
                VideoJob.id.in_(selected_ids)
            )
        ).all()
    )

    if not jobs:
        return RedirectResponse(
            "/videos?success=No matching videos found.",
            status_code=303,
        )

    deleted_count = 0

    for job in jobs:
        cleanup_job_files(
            worker_pid=job.worker_pid,
            output_directory=job.output_directory,
        )

        db.execute(
            delete(YouTubePublishJob).where(
                YouTubePublishJob.video_job_id == job.id
            )
        )

        db.delete(job)
        deleted_count += 1

    db.commit()

    return RedirectResponse(
        f"/videos?success={deleted_count} video(s) and related files deleted.",
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
        return RedirectResponse("/videos?success=Video not found.", status_code=303)

    root = OUTPUT_ROOT.resolve()
    video = (Path(job.output_directory) / "final-video.mp4").resolve()

    if not video.is_file() or root not in video.parents:
        return RedirectResponse("/videos?success=Video not found.", status_code=303)

    def iterfile():
        with open(video, "rb") as file:
            while chunk := file.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": content_disposition_for_video(job.title, job.id),
            "Content-Length": str(video.stat().st_size),
        },
    )


@app.get("/scenes")
def scenes_page(
    request: Request,
    job_id: int | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    jobs_with_scenes = db.scalars(
        select(VideoJob)
        .where(VideoJob.scene_plan.is_not(None))
        .order_by(VideoJob.id.desc())
    ).all()

    selected_job = None

    if job_id is not None:
        selected_job = next(
            (
                job
                for job in jobs_with_scenes
                if job.id == job_id
            ),
            None,
        )

    if selected_job is None and jobs_with_scenes:
        selected_job = jobs_with_scenes[0]

    all_scenes = (
        prepare_scene_items(selected_job)
        if selected_job
        else []
    )

    per_page = 12
    total_scenes = len(all_scenes)
    total_pages = max(
        1,
        (total_scenes + per_page - 1) // per_page,
    )
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    scenes = all_scenes[start:start + per_page]

    providers = sorted({
        scene["provider"]
        for scene in all_scenes
        if scene["provider"]
    })

    scene_stats = {
        "total": total_scenes,
        "videos": sum(
            scene["media_extension"]
            in {".mp4", ".webm", ".mov"}
            for scene in all_scenes
        ),
        "images": sum(
            scene["media_extension"]
            in {".jpg", ".jpeg", ".png", ".webp"}
            for scene in all_scenes
        ),
        "missing": sum(
            not scene["media_available"]
            for scene in all_scenes
        ),
        "duration": round(sum(
            scene["duration_seconds"]
            for scene in all_scenes
        ), 1),
    }

    return templates.TemplateResponse(
        request=request,
        name="scenes.html",
        context={
            "app_name": settings.app_name,
            "active_page": "scenes",
            "project_info": get_project_info(),
            "jobs_with_scenes": jobs_with_scenes,
            "selected_job": selected_job,
            "scenes": scenes,
            "scene_stats": scene_stats,
            "providers": providers,
            "page": page,
            "total_pages": total_pages,
        },
    )


@app.get("/jobs/{job_id}/scenes/{scene_number}/media")
def scene_media_preview(
    job_id: int,
    scene_number: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)
    media_path = (
        resolve_scene_media(job, scene_number)
        if job
        else None
    )

    if not media_path:
        return RedirectResponse(
            f"/scenes?job_id={job_id}&message=Scene media not found.",
            status_code=303,
        )

    media_types = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }

    return FileResponse(
        path=media_path,
        media_type=media_types.get(
            media_path.suffix.lower(),
            "application/octet-stream",
        ),
    )



@app.get("/facebook/connect")
def facebook_connect(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    if not has_api_key("zernio_api_key"):
        return RedirectResponse(
            "/settings?facebook=zernio_required",
            status_code=303,
        )

    user = db.get(
        User,
        int(request.session["user_id"]),
    )

    if user is None:
        return RedirectResponse("/login", status_code=303)

    try:
        profile_id = ensure_user_zernio_profile(
            db,
            user,
        )

        callback_url = (
            get_public_app_url().rstrip("/")
            + "/facebook/sync"
        )

        auth_url = get_facebook_connect_url(
            profile_id,
            redirect_url=callback_url,
        )

        return RedirectResponse(
            auth_url,
            status_code=302,
        )

    except Exception:
        logger.exception(
            "Could not start Facebook connection"
        )
        return RedirectResponse(
            "/publishing?facebook=connection_failed",
            status_code=303,
        )


@app.get("/facebook/sync")
def facebook_sync(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    if request.query_params.get("error"):
        return RedirectResponse(
            "/publishing?facebook=access_denied",
            status_code=303,
        )

    user = db.get(
        User,
        int(request.session["user_id"]),
    )

    if user is None:
        return RedirectResponse("/login", status_code=303)

    try:
        sync_user_facebook_connections(
            db,
            user,
        )
    except Exception:
        logger.exception(
            "Could not sync Facebook connections"
        )
        return RedirectResponse(
            "/publishing?facebook=sync_failed",
            status_code=303,
        )

    return RedirectResponse(
        "/publishing?facebook=connected",
        status_code=303,
    )


@app.post(
    "/facebook/connections/{connection_id}/disconnect"
)
def facebook_disconnect(
    connection_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    user_id = int(request.session["user_id"])

    connection = db.scalar(
        select(FacebookConnection).where(
            FacebookConnection.id == connection_id,
            FacebookConnection.user_id == user_id,
        )
    )

    if connection is None:
        return RedirectResponse(
            "/publishing?facebook=not_found",
            status_code=303,
        )

    try:
        disconnect_account(
            connection.zernio_account_id,
        )
    except ZernioError:
        logger.exception(
            "Could not disconnect Facebook account"
        )
        return RedirectResponse(
            "/publishing?facebook=disconnect_failed",
            status_code=303,
        )

    connection.is_active = False
    db.commit()

    return RedirectResponse(
        "/publishing?facebook=disconnected",
        status_code=303,
    )


@app.get("/publishing")
def publishing_page(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    jobs = load_dashboard_jobs(db)

    user_id = int(request.session["user_id"])

    if has_api_key("zernio_api_key"):
        try:
            current_user = db.get(
                User,
                user_id,
            )

            if current_user is not None:
                sync_user_facebook_connections(
                    db,
                    current_user,
                )
        except Exception:
            # Publishing page must remain usable even if
            # Zernio is temporarily unavailable.
            logger.exception(
                "Automatic Facebook connection sync failed"
            )

    facebook_connections = db.scalars(
        select(FacebookConnection)
        .where(
            FacebookConnection.user_id == user_id,
        )
        .order_by(
            FacebookConnection.connected_at.desc()
        )
    ).all()

    youtube_connections = db.scalars(
        select(YouTubeConnection).order_by(
            YouTubeConnection.connected_at.desc()
        )
    ).all()

    for connection in youtube_connections:
        connection.channel_thumbnail_url = (
            ensure_channel_thumbnail_cached(
                db,
                connection,
            )
        )

    completed_jobs = sorted(
        [
            job
            for job in jobs
            if job.status == "completed"
        ],
        key=lambda job: job.id,
        reverse=True,
    )

    try:
        publishing_page = max(
            1,
            int(request.query_params.get("page", "1")),
        )
    except ValueError:
        publishing_page = 1

    publishing_page_size = 20

    youtube_publish_jobs = db.scalars(
        select(YouTubePublishJob)
        .order_by(YouTubePublishJob.id.desc())
        .offset(
            (publishing_page - 1)
            * publishing_page_size
        )
        .limit(publishing_page_size + 1)
    ).all()

    publishing_has_older = (
        len(youtube_publish_jobs)
        > publishing_page_size
    )
    publishing_has_newer = publishing_page > 1

    youtube_publish_jobs = youtube_publish_jobs[
        :publishing_page_size
    ]

    publishing_items = []

    facebook_publish_jobs = db.scalars(
        select(FacebookPublishJob)
        .order_by(FacebookPublishJob.id.desc())
        .limit(50)
    ).all()

    facebook_publishing_items = []

    for publish_job in facebook_publish_jobs:
        facebook_publishing_items.append({
            "publish_job": publish_job,
            "video_job": db.get(
                VideoJob,
                publish_job.video_job_id,
            ),
            "connection": db.get(
                FacebookConnection,
                publish_job.facebook_connection_id,
            ),
        })

    for publish_job in youtube_publish_jobs:
        publishing_items.append({
            "publish_job": publish_job,
            "video_job": db.get(
                VideoJob,
                publish_job.video_job_id,
            ),
            "connection": db.get(
                YouTubeConnection,
                publish_job.youtube_connection_id,
            ),
        })

    return templates.TemplateResponse(
        request=request,
        name="publishing.html",
        context={
            "app_name": settings.app_name,
            "active_page": "publishing",
            "project_info": get_project_info(),
            "publishing_status": collect_publishing_status(),
            "youtube_connections": youtube_connections,
            "facebook_connections": facebook_connections,
            "zernio_ready": has_api_key("zernio_api_key"),
            "publishing_items": publishing_items,
            "facebook_publishing_items": facebook_publishing_items,
            "publishing_page": publishing_page,
            "publishing_has_older": publishing_has_older,
            "publishing_has_newer": publishing_has_newer,
            "completed_jobs": completed_jobs,
        },
    )


@app.get("/publishing/status")
def youtube_publishing_status(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return {"authenticated": False, "items": []}

    publish_jobs = db.scalars(
        select(YouTubePublishJob).order_by(
            YouTubePublishJob.id.desc()
        )
    ).all()

    return {
        "authenticated": True,
        "items": [
            {
                "id": publish_job.id,
                "status": publish_job.status,
                "progress": publish_job.progress,
                "bytes_uploaded": publish_job.bytes_uploaded,
                "total_bytes": publish_job.total_bytes,
                "last_error": publish_job.last_error,
                "youtube_url": publish_job.youtube_url,
                "is_scheduled": (
                    publish_job.scheduled_at is not None
                ),
            }
            for publish_job in publish_jobs
        ],
    }



@app.get("/publishing/jobs/{job_id}/facebook")
def facebook_publish_draft_page(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    user_id = int(request.session["user_id"])
    job = db.get(VideoJob, job_id)

    if job is None:
        return RedirectResponse(
            "/publishing?facebook=video_not_found",
            status_code=303,
        )

    try:
        resolve_final_video_path(job)
    except YouTubePublishFileError:
        return RedirectResponse(
            "/publishing?facebook=video_not_ready",
            status_code=303,
        )

    facebook_connections = db.scalars(
        select(FacebookConnection)
        .where(
            FacebookConnection.user_id == user_id,
            FacebookConnection.is_active.is_(True),
        )
        .order_by(FacebookConnection.display_name)
    ).all()

    if not facebook_connections:
        return RedirectResponse(
            "/publishing?facebook=no_pages",
            status_code=303,
        )

    existing = db.scalar(
        select(FacebookPublishJob)
        .where(
            FacebookPublishJob.video_job_id == job.id,
            FacebookPublishJob.user_id == user_id,
        )
        .order_by(FacebookPublishJob.id.desc())
    )

    return templates.TemplateResponse(
        request=request,
        name="facebook_publish_draft.html",
        context={
            "app_name": settings.app_name,
            "active_page": "publishing",
            "project_info": get_project_info(),
            "job": job,
            "facebook_connections": facebook_connections,
            "existing": existing,
            "error": None,
        },
    )


@app.post("/publishing/jobs/{job_id}/facebook")
def save_facebook_publish_draft_route(
    job_id: int,
    request: Request,
    facebook_connection_id: int = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    publish_mode: str = Form("now"),
    scheduled_at_local: str = Form(""),
    scheduled_at_utc: str = Form(""),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    user_id = int(request.session["user_id"])
    job = db.get(VideoJob, job_id)

    if job is None:
        return RedirectResponse(
            "/publishing?facebook=video_not_found",
            status_code=303,
        )

    try:
        resolve_final_video_path(job)
    except YouTubePublishFileError:
        return RedirectResponse(
            "/publishing?facebook=video_not_ready",
            status_code=303,
        )

    connection = db.scalar(
        select(FacebookConnection).where(
            FacebookConnection.id == facebook_connection_id,
            FacebookConnection.user_id == user_id,
            FacebookConnection.is_active.is_(True),
        )
    )

    if connection is None:
        return RedirectResponse(
            f"/publishing/jobs/{job_id}/facebook?error=page_unavailable",
            status_code=303,
        )

    publish_mode = publish_mode.strip().lower()

    if publish_mode not in {"now", "scheduled"}:
        publish_mode = "now"

    scheduled_at = None

    if publish_mode == "scheduled":
        if not scheduled_at_utc.strip():
            return RedirectResponse(
                f"/publishing/jobs/{job_id}/facebook?error=schedule_required",
                status_code=303,
            )

        try:
            parsed = datetime.fromisoformat(
                scheduled_at_utc.strip().replace(
                    "Z",
                    "+00:00",
                )
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            parsed = parsed.astimezone(
                timezone.utc
            )

            if parsed <= datetime.now(timezone.utc):
                return RedirectResponse(
                    f"/publishing/jobs/{job_id}/facebook?error=schedule_past",
                    status_code=303,
                )

            scheduled_at = parsed.replace(
                tzinfo=None
            )

        except ValueError:
            return RedirectResponse(
                f"/publishing/jobs/{job_id}/facebook?error=schedule_invalid",
                status_code=303,
            )

    publish_job = db.scalar(
        select(FacebookPublishJob).where(
            FacebookPublishJob.video_job_id == job.id,
            FacebookPublishJob.facebook_connection_id
            == connection.id,
        )
    )

    if publish_job is None:
        publish_job = FacebookPublishJob(
            user_id=user_id,
            video_job_id=job.id,
            facebook_connection_id=connection.id,
            status="draft",
        )
        db.add(publish_job)

    elif publish_job.status in {
        "queued",
        "uploading",
        "publishing",
        "published",
        "scheduled",
    }:
        return RedirectResponse(
            "/publishing?facebook=already_queued",
            status_code=303,
        )

    publish_job.title = (
        title.strip()
        or job.youtube_title
        or job.title
        or "Video"
    )[:255]

    publish_job.description = (
        description.strip()
        or job.youtube_description
        or ""
    )
    publish_job.scheduled_at = scheduled_at
    publish_job.status = "draft"
    publish_job.last_error = None

    db.commit()
    db.refresh(publish_job)

    try:
        queue_facebook_publish_job(
            db,
            publish_job,
        )
    except FacebookPublishQueueError as error:
        return RedirectResponse(
            f"/publishing?facebook={error.code}",
            status_code=303,
        )

    return RedirectResponse(
        "/publishing?facebook=upload_queued",
        status_code=303,
    )


@app.get("/publishing/jobs/{job_id}/draft")
def youtube_publish_draft_page(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)

    if job is None:
        return RedirectResponse(
            "/publishing?youtube=video_not_found",
            status_code=303,
        )

    try:
        resolve_final_video_path(job)
    except YouTubePublishFileError:
        return RedirectResponse(
            "/publishing?youtube=video_not_ready",
            status_code=303,
        )

    youtube_connections = db.scalars(
        select(YouTubeConnection)
        .where(
            YouTubeConnection.is_active.is_(True),
            YouTubeConnection.encrypted_refresh_token.is_not(None),
        )
        .order_by(YouTubeConnection.channel_title)
    ).all()

    default_connection_id = (
        youtube_connections[0].id
        if youtube_connections
        else ""
    )

    form_data = {
        "youtube_connection_id": default_connection_id,
        "title": job.youtube_title or job.title,
        "description": job.youtube_description or "",
        "tags": job.youtube_tags or "",
        "category_id": job.youtube_category_id or "27",
        "language": job.youtube_language or "en",
        "publish_mode": (
            "scheduled"
            if job.auto_publish_scheduled_at
            else (job.auto_publish_privacy or "private")
        ),
        "scheduled_at_local": "",
        "made_for_kids": bool(
            job.auto_publish_made_for_kids
        ),
        "contains_synthetic_media": False,
        "notify_subscribers": bool(
            job.auto_publish_notify_subscribers
        ),
    }

    return templates.TemplateResponse(
        request=request,
        name="youtube_publish_draft.html",
        context={
            "app_name": settings.app_name,
            "active_page": "publishing",
            "project_info": get_project_info(),
            "job": job,
            "youtube_connections": youtube_connections,
            "categories": YOUTUBE_CATEGORY_OPTIONS,
            "form_data": form_data,
            "errors": [],
        },
    )


@app.post("/publishing/jobs/{job_id}/draft")
def save_youtube_publish_draft_route(
    job_id: int,
    request: Request,
    youtube_connection_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    category_id: str = Form("27"),
    language: str = Form("en"),
    publish_mode: str = Form("private"),
    scheduled_at_local: str = Form(""),
    scheduled_at_utc: str = Form(""),
    made_for_kids: str = Form(""),
    contains_synthetic_media: str = Form(""),
    notify_subscribers: str = Form(""),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    job = db.get(VideoJob, job_id)

    if job is None:
        return RedirectResponse(
            "/publishing?youtube=video_not_found",
            status_code=303,
        )

    connection = db.get(
        YouTubeConnection,
        youtube_connection_id,
    )

    youtube_connections = db.scalars(
        select(YouTubeConnection)
        .where(
            YouTubeConnection.is_active.is_(True),
            YouTubeConnection.encrypted_refresh_token.is_not(None),
        )
        .order_by(YouTubeConnection.channel_title)
    ).all()

    form_data = {
        "youtube_connection_id": youtube_connection_id,
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": category_id,
        "language": language,
        "publish_mode": publish_mode,
        "scheduled_at_local": scheduled_at_local,
        "made_for_kids": bool(made_for_kids),
        "contains_synthetic_media": bool(
            contains_synthetic_media
        ),
        "notify_subscribers": bool(notify_subscribers),
    }

    errors = []
    scheduled_at = None
    privacy_status = publish_mode.strip().lower()

    if privacy_status == "scheduled":
        privacy_status = "private"

        if not scheduled_at_utc.strip():
            errors.append(
                "Choose a future date and time for scheduled publishing."
            )
        else:
            try:
                parsed_schedule = datetime.fromisoformat(
                    scheduled_at_utc.strip().replace("Z", "+00:00")
                )

                if parsed_schedule.tzinfo is None:
                    parsed_schedule = parsed_schedule.replace(
                        tzinfo=timezone.utc
                    )

                scheduled_at = (
                    parsed_schedule
                    .astimezone(timezone.utc)
                    .replace(tzinfo=None)
                )

            except ValueError:
                errors.append(
                    "The scheduled publishing date and time is invalid."
                )

    elif privacy_status not in {
        "private",
        "unlisted",
        "public",
    }:
        errors.append(
            "Choose Private, Unlisted, Public, or Scheduled."
        )

    if connection is None:
        errors.append(
            "The selected YouTube channel was not found."
        )
    elif not errors:
        try:
            draft = save_youtube_publish_draft(
                db,
                video_job=job,
                connection=connection,
                title=title,
                description=description,
                tags=tags,
                category_id=category_id,
                language=language,
                privacy_status=privacy_status,
                made_for_kids=bool(made_for_kids),
                contains_synthetic_media=bool(
                    contains_synthetic_media
                ),
                notify_subscribers=bool(
                    notify_subscribers
                ),
                scheduled_at=scheduled_at,
            )
        except YouTubePublishValidationError as exc:
            db.rollback()
            errors.extend(exc.errors)
        except YouTubePublishFileError as exc:
            db.rollback()
            errors.append(str(exc))
        except Exception:
            db.rollback()
            logger.exception(
                "Could not save YouTube publishing draft"
            )
            errors.append(
                "The publishing draft could not be saved."
            )
        else:
            return RedirectResponse(
                f"/publishing?youtube=draft_saved"
                f"&draft_id={draft.id}",
                status_code=303,
            )

    return templates.TemplateResponse(
        request=request,
        name="youtube_publish_draft.html",
        context={
            "app_name": settings.app_name,
            "active_page": "publishing",
            "project_info": get_project_info(),
            "job": job,
            "youtube_connections": youtube_connections,
            "categories": YOUTUBE_CATEGORY_OPTIONS,
            "form_data": form_data,
            "errors": errors,
        },
        status_code=422,
    )


@app.post("/publishing/drafts/{draft_id}/queue")
def queue_youtube_private_upload(
    draft_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    publish_job = db.get(
        YouTubePublishJob,
        draft_id,
    )

    if publish_job is None:
        return RedirectResponse(
            "/publishing?youtube=draft_not_found",
            status_code=303,
        )

    try:
        queue_youtube_publish_job(
            db,
            publish_job,
        )
    except YouTubePublishQueueError as error:
        return RedirectResponse(
            f"/publishing?youtube={error.code}",
            status_code=303,
        )

    return RedirectResponse(
        f"/publishing?youtube=upload_queued"
        f"&draft_id={publish_job.id}",
        status_code=303,
    )


@app.get("/youtube/connect")
def connect_youtube_channel(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    try:
        authorization_url, state, code_verifier = (
            create_youtube_authorization_url()
        )
    except YouTubeOAuthConfigurationError:
        return RedirectResponse(
            "/publishing?youtube=credentials_required",
            status_code=303,
        )

    request.session["youtube_oauth_state"] = state
    request.session[
        "youtube_oauth_code_verifier"
    ] = code_verifier

    return RedirectResponse(
        authorization_url,
        status_code=303,
    )


@app.get("/youtube/oauth/callback")
def youtube_oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    if request.query_params.get("error"):
        request.session.pop("youtube_oauth_state", None)
        request.session.pop(
            "youtube_oauth_code_verifier",
            None,
        )
        return RedirectResponse(
            "/publishing?youtube=access_denied",
            status_code=303,
        )

    returned_state = request.query_params.get("state", "")
    authorization_code = request.query_params.get("code", "")
    stored_state = request.session.pop(
        "youtube_oauth_state",
        None,
    )
    stored_code_verifier = request.session.pop(
        "youtube_oauth_code_verifier",
        None,
    )

    if (
        not stored_state
        or returned_state != stored_state
        or not authorization_code
        or not stored_code_verifier
    ):
        return RedirectResponse(
            "/publishing?youtube=invalid_state",
            status_code=303,
        )

    try:
        flow = build_youtube_oauth_flow(
            state=stored_state,
            code_verifier=stored_code_verifier,
        )
        flow.fetch_token(code=authorization_code)
        save_connected_channel(db, flow.credentials)
    except Exception:
        db.rollback()
        logger.exception("YouTube OAuth callback failed")
        return RedirectResponse(
            "/publishing?youtube=connection_failed",
            status_code=303,
        )

    return RedirectResponse(
        "/publishing?youtube=connected",
        status_code=303,
    )


@app.post("/youtube/connections/{connection_id}/disconnect")
def disconnect_youtube_channel(
    connection_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    connection = db.get(YouTubeConnection, connection_id)

    if connection is None:
        return RedirectResponse(
            "/publishing?youtube=not_found",
            status_code=303,
        )

    connection.is_active = False
    connection.encrypted_access_token = None
    connection.encrypted_refresh_token = None
    connection.token_expiry = None
    connection.last_error = None

    db.commit()

    return RedirectResponse(
        "/publishing?youtube=disconnected",
        status_code=303,
    )


@app.get("/system-health")
def system_health_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="system_health.html",
        context={
            "app_name": settings.app_name,
            "active_page": "health",
            "project_info": get_project_info(),
            "system_metrics": collect_dashboard_metrics(),
        },
    )


_VIDEO_PRESETS_FILE = PROJECT_ROOT / "backend" / "data" / "video_presets.json"


def _read_video_presets():
    try:
        if not _VIDEO_PRESETS_FILE.is_file():
            return {}
        data = json.loads(_VIDEO_PRESETS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("Could not read video presets")
        return {}


def _write_video_presets(data):
    _VIDEO_PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _VIDEO_PRESETS_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(_VIDEO_PRESETS_FILE)


@app.get("/video-presets/data")
def video_presets_data(request: Request):
    if not request.session.get("user_id"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(_read_video_presets())


@app.post("/video-presets/data")
async def video_presets_save(request: Request):
    if not request.session.get("user_id"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if not isinstance(data, dict):
        return JSONResponse({"error": "Invalid preset data"}, status_code=400)

    # Never allow topic/script content into reusable presets.
    cleaned = {}
    for name, preset in data.items():
        name = str(name).strip()[:60]
        if not name or not isinstance(preset, dict):
            continue

        settings_data = preset.get("settings", {})
        if not isinstance(settings_data, dict):
            settings_data = {}

        settings_data = {
            key: value
            for key, value in settings_data.items()
            if key not in {"title", "titles", "source_script"}
        }

        cleaned[name] = {
            "settings": settings_data,
            "updated_at": str(preset.get("updated_at", "")),
        }

    _write_video_presets(cleaned)
    return JSONResponse({"ok": True, "count": len(cleaned)})


_BACKUP_NAME = re.compile(r"^atta-user-data-[0-9]{8}-[0-9]{6}\.tar\.gz$")
_BACKUPS_DIR = PROJECT_ROOT / "backups"


def _backup_archives():
    if not _BACKUPS_DIR.is_dir():
        return []
    items = []
    for path in sorted(_BACKUPS_DIR.glob("atta-user-data-*.tar.gz"), reverse=True):
        if not _BACKUP_NAME.match(path.name):
            continue
        stat = path.stat()
        items.append({
            "name": path.name,
            "size": f"{stat.st_size / (1024 * 1024):.1f} MB",
            "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return items


def _backup_page(request, message="", error=""):
    return templates.TemplateResponse(
        request=request,
        name="backup.html",
        context={
            "app_name": settings.app_name,
            "active_page": "backup",
            "project_info": get_project_info(),
            "archives": _backup_archives(),
            "message": message,
            "error": error,
        },
    )


@app.get("/backup")
def backup_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    return _backup_page(
        request,
        message=request.query_params.get("message", ""),
        error=request.query_params.get("error", ""),
    )


@app.post("/backup/create")
def backup_create(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    script = PROJECT_ROOT / "scripts" / "backup_user_data.sh"
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=300,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0 or "BACKUP_OK" not in result.stdout:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-200:]
        return RedirectResponse(f"/backup?error=Backup failed: {quote(detail)}", status_code=303)
    return RedirectResponse("/backup?message=Backup created.", status_code=303)


@app.get("/backup/download/{name}")
def backup_download(request: Request, name: str):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    if not _BACKUP_NAME.match(name):
        return RedirectResponse("/backup?error=Invalid archive name.", status_code=303)
    path = _BACKUPS_DIR / name
    if not path.is_file():
        return RedirectResponse("/backup?error=Archive not found.", status_code=303)
    return FileResponse(path, filename=name, media_type="application/gzip")


@app.post("/backup/upload")
async def backup_upload(request: Request, archive: UploadFile = File(...)):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    name = Path(archive.filename or "").name
    if not _BACKUP_NAME.match(name):
        return RedirectResponse(
            "/backup?error=File must be an atta-user-data-*.tar.gz archive.", status_code=303,
        )
    _BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    destination = _BACKUPS_DIR / name
    with destination.open("wb") as handle:
        while chunk := await archive.read(1024 * 1024):
            handle.write(chunk)
    return RedirectResponse(f"/backup?message=Uploaded {quote(name)}.", status_code=303)


@app.post("/backup/restore")
def backup_restore(request: Request, name: str = Form(...)):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)
    if not _BACKUP_NAME.match(name) or not (_BACKUPS_DIR / name).is_file():
        return RedirectResponse("/backup?error=Archive not found.", status_code=303)
    script = PROJECT_ROOT / "scripts" / "restore_user_data.sh"
    log_path = _BACKUPS_DIR / "restore.log"
    # The restore stops and restarts this very app, so it must run outside this
    # service's cgroup or systemd kills it mid-restore. A transient unit via
    # systemd-run survives the restart it performs.
    unit = f"atta-restore-{datetime.now():%Y%m%d%H%M%S}"
    command = (
        f"bash {shlex.quote(str(script))} {shlex.quote(str(_BACKUPS_DIR / name))} "
        f">> {shlex.quote(str(log_path))} 2>&1"
    )
    launch = subprocess.run(
        ["sudo", "-n", "systemd-run", "--collect", f"--unit={unit}",
         "bash", "-c", command],
        capture_output=True, text=True, timeout=30,
    )
    if launch.returncode != 0:
        detail = (launch.stderr or launch.stdout or "systemd-run failed").strip()[-200:]
        return _backup_page(request, error=f"Could not start restore: {detail}")
    return _backup_page(
        request,
        message=(
            "Restore started. The app will restart; refresh this page in about "
            "30 seconds. Details land in backups/restore.log."
        ),
    )


@app.get("/app-info")
def public_app_info_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="homepage.html",
        context={
            "app_name": "Atta Video AI",
        },
    )


@app.get("/privacy-policy")
def privacy_policy_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="privacy_policy.html",
        context={
            "app_name": settings.app_name,
            "active_page": "",
            "project_info": get_project_info(),
        },
    )


@app.get("/terms")
def terms_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="terms.html",
        context={
            "app_name": settings.app_name,
            "active_page": "",
            "project_info": get_project_info(),
        },
    )


@app.get("/about-developer")
def about_developer_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="about_developer.html",
        context={
            "app_name": settings.app_name,
            "active_page": "about",
            "project_info": get_project_info(),
        },
    )


@app.get("/settings")
def api_settings_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    pexels_ready = has_api_key("pexels_api_key")
    pixabay_ready = has_api_key("pixabay_api_key")
    coverr_ready = has_api_key("coverr_api_key")
    openverse_id_ready = has_api_key("openverse_client_id")
    openverse_secret_ready = has_api_key(
        "openverse_client_secret"
    )
    gemini_ready = has_api_key("gemini_api_key")
    together_ready = has_api_key("together_api_key")
    youtube_client_id_ready = has_api_key("youtube_client_id")
    youtube_client_secret_ready = has_api_key(
        "youtube_client_secret"
    )
    zernio_ready = has_api_key("zernio_api_key")
    configured_count = sum([
        pexels_ready,
        pixabay_ready,
        coverr_ready,
        openverse_id_ready and openverse_secret_ready,
        gemini_ready,
        together_ready,
        youtube_client_id_ready and youtube_client_secret_ready,
        zernio_ready,
    ])

    return templates.TemplateResponse(
        request=request,
        name="api_settings.html",
        context={
            "app_name": settings.app_name,
            "active_page": "settings",
            "project_info": get_project_info(),
            "pexels_ready": pexels_ready,
            "pixabay_ready": pixabay_ready,
            "coverr_ready": coverr_ready,
            "openverse_id_ready": openverse_id_ready,
            "openverse_secret_ready": openverse_secret_ready,
            "gemini_ready": gemini_ready,
            "together_ready": together_ready,
            "public_app_url": get_public_app_url(),
            "youtube_client_id_ready": youtube_client_id_ready,
            "youtube_client_secret_ready": youtube_client_secret_ready,
            "zernio_ready": zernio_ready,
            "configured_count": configured_count,
            "total_integrations": 8,
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
    public_app_url: str = Form(""),
    youtube_client_id: str = Form(""),
    youtube_client_secret: str = Form(""),
    zernio_api_key: str = Form(""),
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
    if public_app_url.strip():
        save_api_key(
            "public_app_url",
            normalize_public_app_url(public_app_url),
        )

    save_api_key("youtube_client_id", youtube_client_id.strip())
    save_api_key(
        "youtube_client_secret",
        youtube_client_secret.strip(),
    )
    save_api_key("zernio_api_key", zernio_api_key.strip())
    clear_quota_cache()

    return RedirectResponse("/settings?saved=1", status_code=303)


ALLOWED_API_SETTINGS = {
    "pexels_api_key",
    "pixabay_api_key",
    "coverr_api_key",
    "openverse_client_id",
    "openverse_client_secret",
    "gemini_api_key",
    "together_api_key",
    "public_app_url",
    "youtube_client_id",
    "youtube_client_secret",
    "zernio_api_key",
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
    if removed:
        clear_quota_cache()

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
            "/videos?success=Narration not found.", status_code=303
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
            "/videos?success=Script not found.", status_code=303
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
            "/videos?success=Caption file not found.",
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
            "/videos?success=Caption file not found.",
            status_code=303,
        )

    return FileResponse(
        str(subtitle),
        media_type="text/vtt",
        filename=f"captions-job-{job_id}.vtt",
    )



@app.post("/profile/recovery-code")
def generate_profile_recovery_code(
    request: Request,
    current_password: str = Form(...),
    db: Session = Depends(get_db),
):
    import secrets
    from datetime import datetime, timezone

    if not request.session.get("user_id"):
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    user = db.get(
        User,
        int(request.session["user_id"]),
    )

    if user is None:
        request.session.clear()

        return RedirectResponse(
            "/login",
            status_code=303,
        )

    if not verify_password(
        current_password,
        user.password_hash,
    ):
        return RedirectResponse(
            "/profile?recovery=invalid_password",
            status_code=303,
        )

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    raw_code = "".join(
        secrets.choice(alphabet)
        for _ in range(16)
    )

    display_code = "-".join(
        raw_code[i:i + 4]
        for i in range(0, 16, 4)
    )

    user.recovery_code_hash = (
        _recovery_code_digest(raw_code)
    )

    user.recovery_code_created_at = (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
    )

    db.commit()

    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "app_name": settings.app_name,
            "active_page": "profile",
            "project_info": get_project_info(),
            "user": user,
            "error": None,
            "success": (
                "New recovery code generated. "
                "Save it somewhere safe."
            ),
            "recovery_code": display_code,
        },
    )



@app.get("/voices")
def voice_studio_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=303)

    import sqlite3
    from app.voice_registry import APPROVED_NARRATOR_VOICES
    from app.paths import PROJECT_ROOT

    db_path = PROJECT_ROOT / "backend" / "data" / "app.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cloned_rows = conn.execute(
        "SELECT * FROM cloned_voices ORDER BY created_at DESC"
    ).fetchall()
    tts_rows = conn.execute(
        "SELECT * FROM tts_generations ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    cloned = [dict(v) for v in cloned_rows]
    tts_history = [dict(v) for v in tts_rows]

    preview_dir = Path(__file__).parent / "static" / "voice-previews"
    preview_files = set()
    if preview_dir.exists():
        preview_files = {f.stem for f in preview_dir.glob("*.mp3")}

    from app.project_info import get_project_info
    from app.models import User
    from app.database import SessionLocal as SL

    with SL() as db:
        user = db.get(User, request.session.get("user_id"))

    return templates.TemplateResponse(
        request=request,
        name="voice_studio.html",
        context={
            "app_name": "Atta Video AI",
            "active_page": "voices",
            "cloned_voices": cloned,
            "builtin_voices": APPROVED_NARRATOR_VOICES,
            "preview_files": preview_files,
            "tts_history": tts_history,
            "project_info": get_project_info(),
            "user": user,
        },
    )


@app.post("/voices/upload")
async def upload_clone_voice(
    request: Request,
    name: str = Form(...),
    reference_audio: UploadFile = File(...),
    exaggeration: float = Form(0.5),
):
    if not request.session.get("user_id"):
        return JSONResponse({"success": False, "error": "Not authenticated"}, 401)

    import sqlite3
    from app.paths import PROJECT_ROOT

    user_id = request.session["user_id"]

    # Save reference audio
    voices_dir = Path(__file__).parent / "static" / "cloned-voices"
    voices_dir.mkdir(parents=True, exist_ok=True)

    # Insert record first to get ID
    db_path = PROJECT_ROOT / "backend" / "data" / "app.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "INSERT INTO cloned_voices (user_id, name, reference_audio_path, exaggeration, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, name, "", exaggeration, "processing"),
    )
    voice_id = cursor.lastrowid
    conn.commit()

    # Save file
    voice_dir = voices_dir / str(voice_id)
    voice_dir.mkdir(parents=True, exist_ok=True)
    ref_path = voice_dir / f"reference{Path(reference_audio.filename).suffix}"

    content = await reference_audio.read()
    ref_path.write_bytes(content)

    # Convert to WAV if needed
    ref_wav = voice_dir / "reference.wav"
    if ref_path.suffix.lower() != ".wav":
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(ref_path), "-ar", "24000", "-ac", "1", str(ref_wav)],
            check=True,
        )
    else:
        import shutil
        shutil.copy2(str(ref_path), str(ref_wav))

    # Update path
    conn.execute(
        "UPDATE cloned_voices SET reference_audio_path = ? WHERE id = ?",
        (str(ref_wav), voice_id),
    )
    conn.commit()

    # Generate preview
    try:
        from app.chatterbox_service import generate_preview

        preview_wav = voice_dir / "preview.wav"
        preview_mp3 = voice_dir / "preview.mp3"

        generate_preview(
            reference_audio_path=str(ref_wav),
            output_path=str(preview_wav),
        )

        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(preview_wav), "-b:a", "128k", str(preview_mp3)],
            check=True,
        )
        preview_wav.unlink(missing_ok=True)

        conn.execute(
            "UPDATE cloned_voices SET preview_audio_path = ?, status = ? WHERE id = ?",
            (str(preview_mp3), "ready", voice_id),
        )
        conn.commit()

    except Exception as e:
        conn.execute(
            "UPDATE cloned_voices SET status = ?, error_message = ? WHERE id = ?",
            ("error", str(e), voice_id),
        )
        conn.commit()
        conn.close()
        return JSONResponse({"success": False, "error": str(e)})

    conn.close()
    return JSONResponse({"success": True, "voice_id": voice_id})


@app.post("/voices/preview/{voice_id}")
def preview_cloned_voice(voice_id: int, request: Request):
    if not request.session.get("user_id"):
        return JSONResponse({"success": False, "error": "Not authenticated"}, 401)

    import sqlite3
    from app.paths import PROJECT_ROOT

    db_path = PROJECT_ROOT / "backend" / "data" / "app.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    voice = conn.execute(
        "SELECT * FROM cloned_voices WHERE id = ?", (voice_id,)
    ).fetchone()

    if not voice:
        conn.close()
        return JSONResponse({"success": False, "error": "Voice not found"})

    try:
        from app.chatterbox_service import generate_preview

        voice_dir = Path(__file__).parent / "static" / "cloned-voices" / str(voice_id)
        voice_dir.mkdir(parents=True, exist_ok=True)

        preview_wav = voice_dir / "preview.wav"
        preview_mp3 = voice_dir / "preview.mp3"

        generate_preview(
            reference_audio_path=voice["reference_audio_path"],
            output_path=str(preview_wav),
        )

        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(preview_wav), "-b:a", "128k", str(preview_mp3)],
            check=True,
        )
        preview_wav.unlink(missing_ok=True)

        conn.execute(
            "UPDATE cloned_voices SET preview_audio_path = ?, status = ? WHERE id = ?",
            (str(preview_mp3), "ready", voice_id),
        )
        conn.commit()
        conn.close()

        return JSONResponse({"success": True})

    except Exception as e:
        conn.close()
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/voices/delete/{voice_id}")
def delete_cloned_voice(voice_id: int, request: Request):
    if not request.session.get("user_id"):
        return JSONResponse({"success": False, "error": "Not authenticated"}, 401)

    import sqlite3, shutil
    from app.paths import PROJECT_ROOT

    db_path = PROJECT_ROOT / "backend" / "data" / "app.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM cloned_voices WHERE id = ?", (voice_id,))
    conn.commit()
    conn.close()

    voice_dir = Path(__file__).parent / "static" / "cloned-voices" / str(voice_id)
    if voice_dir.exists():
        shutil.rmtree(str(voice_dir))

    return JSONResponse({"success": True})


@app.post("/text-to-voice/generate")
async def text_to_voice_generate(
    request: Request,
    text: str = Form(...),
    voice: str = Form(...),
    speed: float = Form(1.0),
    format: str = Form("mp3"),
    title: str = Form("Untitled"),
):
    if not request.session.get("user_id"):
        return JSONResponse({"success": False, "error": "Not authenticated"}, 401)

    import sqlite3, uuid, subprocess
    from app.paths import PROJECT_ROOT

    output_dir = Path(__file__).parent / "static" / "tts-output"
    output_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4().hex[:12]
    wav_path = output_dir / f"{file_id}.wav"
    out_path = output_dir / f"{file_id}.{format}"

    try:
        if voice.startswith("cloned:"):
            # Cloned voice via Chatterbox
            voice_id = int(voice.split(":")[1])
            db_path = PROJECT_ROOT / "backend" / "data" / "app.db"
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            v = conn.execute(
                "SELECT * FROM cloned_voices WHERE id = ?", (voice_id,)
            ).fetchone()
            conn.close()

            if not v:
                return JSONResponse({"success": False, "error": "Voice not found"})

            from app.chatterbox_service import generate_cloned_voice
            generate_cloned_voice(
                text=text,
                reference_audio_path=v["reference_audio_path"],
                output_path=str(wav_path),
                exaggeration=v["exaggeration"] or 0.5,
            )

        elif voice.startswith("builtin:"):
            # Built-in Kokoro voice
            voice_id = voice.split(":")[1]
            from app.kokoro_service import generate_narration
            generate_narration(text, voice_id, speed, wav_path)

        else:
            return JSONResponse({"success": False, "error": "Invalid voice selection"})

        # Convert to requested format
        if format == "mp3":
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(wav_path), "-b:a", "128k", str(out_path)],
                check=True,
            )
            wav_path.unlink(missing_ok=True)
        else:
            out_path = wav_path

        filename = f"tts-{file_id}.{format}"
        audio_url = f"/static/tts-output/{file_id}.{format}"

        # Save to history
        try:
            voice_name = voice.split(":")[1] if ":" in voice else voice
            voice_type = "cloned" if voice.startswith("cloned:") else "builtin"
            if voice_type == "cloned":
                cv_row = conn2 if 'conn2' in dir() else None
                import sqlite3 as sq
                hconn = sq.connect(str(db_path))
                hconn.row_factory = sq.Row
                cv_info = hconn.execute("SELECT name FROM cloned_voices WHERE id=?", (int(voice_name),)).fetchone()
                voice_name = cv_info["name"] if cv_info else f"Cloned #{voice_name}"
                hconn.close()

            dur_r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(out_path)],
                capture_output=True, text=True
            )
            duration = float(dur_r.stdout.strip()) if dur_r.stdout.strip() else 0

            import sqlite3 as sq2
            hconn2 = sq2.connect(str(db_path))
            hconn2.execute(
                "INSERT INTO tts_generations (user_id, voice_name, voice_type, text_preview, word_count, duration_seconds, audio_path, format, title) VALUES (?,?,?,?,?,?,?,?,?)",
                (request.session.get("user_id", 1), voice_name, voice_type, text[:200], len(text.split()), duration, str(out_path), format, title)
            )
            hconn2.commit()
            hconn2.close()
        except Exception as hist_err:
            print(f"TTS history save failed: {hist_err}", flush=True)

        return JSONResponse({
            "success": True,
            "audio_url": audio_url,
            "filename": filename,
        })

    except Exception as e:
        wav_path.unlink(missing_ok=True)
        return JSONResponse({"success": False, "error": str(e)})




@app.post("/tts/delete/{gen_id}")
def delete_tts_generation(gen_id: int, request: Request):
    if not request.session.get("user_id"):
        return JSONResponse({"success": False, "error": "Not authenticated"}, 401)
    import sqlite3
    from app.paths import PROJECT_ROOT
    db_path = PROJECT_ROOT / "backend" / "data" / "app.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    gen = conn.execute("SELECT audio_path FROM tts_generations WHERE id=?", (gen_id,)).fetchone()
    if gen and gen["audio_path"]:
        Path(gen["audio_path"]).unlink(missing_ok=True)
    conn.execute("DELETE FROM tts_generations WHERE id=?", (gen_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})
