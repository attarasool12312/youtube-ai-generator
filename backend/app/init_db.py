from sqlalchemy import inspect, select, text

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password



USER_COLUMN_MIGRATIONS = {
    "zernio_profile_id": "VARCHAR(100)",
    "recovery_code_hash": "VARCHAR(64)",
    "recovery_code_created_at": "DATETIME",
}


VIDEO_JOB_COLUMN_MIGRATIONS = {
    "input_mode": "VARCHAR(20) NOT NULL DEFAULT 'title'",
    "source_script": "TEXT",
    "media_source": "VARCHAR(20) NOT NULL DEFAULT 'stock_only'",
    "captions_enabled": "BOOLEAN NOT NULL DEFAULT 1",
    "caption_font_size": "INTEGER NOT NULL DEFAULT 36",
    "caption_position": "VARCHAR(20) NOT NULL DEFAULT 'bottom'",
    "caption_background": "BOOLEAN NOT NULL DEFAULT 1",
    "caption_outline": "INTEGER NOT NULL DEFAULT 2",
    "avatar_id": "INTEGER",
    "avatar_position": "VARCHAR(20) NOT NULL DEFAULT 'bottom_right'",
    "started_at": "DATETIME",
    "completed_at": "DATETIME",
    "auto_publish_enabled": "BOOLEAN NOT NULL DEFAULT 0",
    "auto_publish_connection_id": "INTEGER",
    "auto_publish_privacy": "VARCHAR(20) NOT NULL DEFAULT 'private'",
    "auto_publish_made_for_kids": "BOOLEAN NOT NULL DEFAULT 0",
    "auto_publish_notify_subscribers": "BOOLEAN NOT NULL DEFAULT 0",
    "auto_publish_metadata": "BOOLEAN NOT NULL DEFAULT 1",
    "auto_publish_thumbnail": "BOOLEAN NOT NULL DEFAULT 0",
    "thumbnail_headline": "VARCHAR(100)",
    "thumbnail_prompt": "TEXT",
    "thumbnail_prompt_provider": "VARCHAR(100)",
    "youtube_title": "VARCHAR(100)",
    "youtube_description": "TEXT",
    "youtube_tags": "TEXT",
    "youtube_category_id": "VARCHAR(20)",
    "youtube_language": "VARCHAR(10)",
    "youtube_metadata_provider": "VARCHAR(100)",
    "youtube_stock_credits": "TEXT",
    "auto_publish_scheduled_at": "DATETIME",
    "manual_thumbnail_path": "TEXT",
    "manual_thumbnail_uploaded_at": "DATETIME",
    "facebook_auto_publish_enabled": "BOOLEAN NOT NULL DEFAULT 0",
    "facebook_auto_publish_connection_id": "INTEGER",
    "facebook_auto_publish_scheduled_at": "DATETIME",
}


YOUTUBE_PUBLISH_JOB_COLUMN_MIGRATIONS = {
    "scheduled_at": "DATETIME",
    "encrypted_upload_session_uri": "TEXT",
    "bytes_uploaded": "INTEGER NOT NULL DEFAULT 0",
    "total_bytes": "INTEGER NOT NULL DEFAULT 0",
    "youtube_video_id": "VARCHAR(100)",
    "youtube_url": "TEXT",
    "thumbnail_status": "VARCHAR(30) NOT NULL DEFAULT 'not_started'",
    "thumbnail_error": "TEXT",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "last_error": "TEXT",
    "started_at": "DATETIME",
    "completed_at": "DATETIME",
}


def migrate_youtube_publish_jobs():
    with engine.begin() as connection:
        inspector = inspect(connection)

        if "youtube_publish_jobs" not in inspector.get_table_names():
            return

        columns = {
            column["name"]
            for column in inspector.get_columns(
                "youtube_publish_jobs"
            )
        }

        for (
            column_name,
            definition,
        ) in YOUTUBE_PUBLISH_JOB_COLUMN_MIGRATIONS.items():
            if column_name in columns:
                continue

            connection.execute(
                text(
                    "ALTER TABLE youtube_publish_jobs "
                    f"ADD COLUMN {column_name} {definition}"
                )
            )

            print(
                "Added youtube_publish_jobs."
                + column_name
            )


def migrate_users():
    with engine.begin() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("users")
        }

        for column_name, definition in USER_COLUMN_MIGRATIONS.items():
            if column_name in columns:
                continue

            connection.execute(
                text(
                    f"ALTER TABLE users "
                    f"ADD COLUMN {column_name} {definition}"
                )
            )
            print(f"Added users.{column_name}")


def migrate_video_jobs():
    with engine.begin() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("video_jobs")
        }

        for column_name, definition in VIDEO_JOB_COLUMN_MIGRATIONS.items():
            if column_name in columns:
                continue

            connection.execute(
                text(
                    f"ALTER TABLE video_jobs "
                    f"ADD COLUMN {column_name} {definition}"
                )
            )
            print(f"Added video_jobs.{column_name}")


def initialize_database():
    Base.metadata.create_all(bind=engine)
    migrate_users()
    migrate_video_jobs()
    migrate_youtube_publish_jobs()

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == settings.admin_username))
        if admin:
            print("Admin user already exists.")
            return
        if not settings.initial_admin_password:
            raise RuntimeError("Initial admin password is missing.")
        db.add(User(
            username=settings.admin_username,
            email=settings.admin_email,
            password_hash=hash_password(settings.initial_admin_password),
        ))
        db.commit()
        print("Database and admin user created successfully.")


if __name__ == "__main__":
    initialize_database()
