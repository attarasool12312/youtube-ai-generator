from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


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
    voice_mode: Mapped[str] = mapped_column(String(20), default="multi")
    narrator_voice: Mapped[str] = mapped_column(String(50), default="am_michael")
    voice_pool: Mapped[str] = mapped_column(Text, default="am_michael,am_fenrir,am_puck,af_heart,af_bella,af_nicole")
    voice_speed: Mapped[float] = mapped_column(Float, default=1.0)
    captions_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    caption_font_size: Mapped[int] = mapped_column(Integer, default=48)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ApiSetting(Base):
    __tablename__ = "api_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )
