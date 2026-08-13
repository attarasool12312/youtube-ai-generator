from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.voice_registry import DEFAULT_NARRATOR_VOICE, DEFAULT_VOICE_POOL_CSV


def utc_now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=10)
    video_style: Mapped[str] = mapped_column(String(30), default="auto")
    orientation: Mapped[str] = mapped_column(String(20), default="landscape")
    media_source: Mapped[str] = mapped_column(String(20), default="stock_only")
    voice_mode: Mapped[str] = mapped_column(String(20), default="multi")
    narrator_voice: Mapped[str] = mapped_column(String(50), default=DEFAULT_NARRATOR_VOICE)
    voice_pool: Mapped[str] = mapped_column(Text, default=DEFAULT_VOICE_POOL_CSV)
    voice_speed: Mapped[float] = mapped_column(Float, default=1.0)
    captions_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    caption_font_size: Mapped[int] = mapped_column(Integer, default=36)
    caption_position: Mapped[str] = mapped_column(
        String(20), default="bottom"
    )
    caption_background: Mapped[bool] = mapped_column(
        Boolean, default=True
    )
    caption_outline: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(50), default="waiting")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    queue_position: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
    scene_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_directory: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    thumbnail_headline: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    thumbnail_prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    thumbnail_prompt_provider: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    youtube_title: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    youtube_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    youtube_tags: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    youtube_category_id: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    youtube_language: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )
    youtube_metadata_provider: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    youtube_stock_credits: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    manual_thumbnail_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    manual_thumbnail_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    auto_publish_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    auto_publish_connection_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    auto_publish_privacy: Mapped[str] = mapped_column(
        String(20),
        default="private",
    )
    auto_publish_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    auto_publish_made_for_kids: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    auto_publish_notify_subscribers: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    auto_publish_metadata: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    auto_publish_thumbnail: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ApiSetting(Base):
    __tablename__ = "api_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class YouTubeConnection(Base):
    __tablename__ = "youtube_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
    )
    channel_title: Mapped[str] = mapped_column(String(255))
    channel_thumbnail_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    google_account_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    encrypted_access_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    encrypted_refresh_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    token_expiry: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    scopes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )


class YouTubePublishJob(Base):
    __tablename__ = "youtube_publish_jobs"
    __table_args__ = (
        UniqueConstraint(
            "video_job_id",
            "youtube_connection_id",
            name="uq_youtube_publish_video_channel",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    video_job_id: Mapped[int] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"),
        index=True,
    )
    youtube_connection_id: Mapped[int] = mapped_column(
        ForeignKey("youtube_connections.id"),
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="draft",
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)

    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    category_id: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    language: Mapped[str] = mapped_column(String(20), default="en")
    privacy_status: Mapped[str] = mapped_column(
        String(20),
        default="private",
    )
    made_for_kids: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    contains_synthetic_media: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    notify_subscribers: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    stock_credits: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    thumbnail_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    encrypted_upload_session_uri: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    bytes_uploaded: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)

    youtube_video_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )
    youtube_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    thumbnail_status: Mapped[str] = mapped_column(
        String(30),
        default="not_started",
    )
    thumbnail_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )
