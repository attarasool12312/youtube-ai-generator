#!/usr/bin/env python3

from getpass import getpass

from sqlalchemy import select

from app.database import SessionLocal
from app.models import User
from app.security import hash_password


def main():
    username = input(
        "Username to reset: "
    ).strip()

    if not username:
        raise SystemExit("Username is required.")

    with SessionLocal() as db:
        user = db.scalar(
            select(User).where(
                User.username == username
            )
        )

        if user is None:
            raise SystemExit("User not found.")

        password = getpass(
            "New password: "
        )

        confirm = getpass(
            "Confirm new password: "
        )

        if len(password) < 8:
            raise SystemExit(
                "Password must be at least 8 characters."
            )

        if password != confirm:
            raise SystemExit(
                "Passwords do not match."
            )

        user.password_hash = hash_password(
            password
        )

        user.recovery_code_hash = None
        user.recovery_code_created_at = None

        db.commit()

        print(
            f"Password reset successfully for {username}."
        )


if __name__ == "__main__":
    main()
