from sqlalchemy import inspect, select, text

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password



VIDEO_JOB_COLUMN_MIGRATIONS = {
    "media_source": "VARCHAR(20) NOT NULL DEFAULT 'mixed'",
    "captions_enabled": "BOOLEAN NOT NULL DEFAULT 1",
    "caption_font_size": "INTEGER NOT NULL DEFAULT 36",
    "caption_position": "VARCHAR(20) NOT NULL DEFAULT 'bottom'",
    "caption_background": "BOOLEAN NOT NULL DEFAULT 1",
    "caption_outline": "INTEGER NOT NULL DEFAULT 2",
    "started_at": "DATETIME",
    "completed_at": "DATETIME",
}


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
    migrate_video_jobs()

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
