from sqlalchemy import select

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.security import hash_password


def initialize_database():
    Base.metadata.create_all(bind=engine)
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
