from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FacebookConnection, User
from app.zernio_client import (
    create_profile,
    list_facebook_accounts,
    list_profiles,
)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_user_zernio_profile(
    db: Session,
    user: User,
) -> str:
    if user.zernio_profile_id:
        return user.zernio_profile_id

    profile_name = f"atta-user-{user.id}"

    # Fresh VM recovery:
    # If this Zernio account already has exactly one profile,
    # safely adopt it instead of creating a duplicate profile.
    all_profiles = list_profiles()

    if len(all_profiles) == 1:
        profile = all_profiles[0]
    else:
        existing_profiles = list_profiles(
            name=profile_name
        )

        profile = next(
            (
                item
                for item in existing_profiles
                if item.get("name") == profile_name
            ),
            None,
        )

        if profile is None:
            profile = create_profile(
                profile_name,
                description=f"Atta Video AI user {user.id}",
                idempotency_key=f"atta-user-profile-{user.id}",
            )

    profile_id = profile.get("_id")

    if not profile_id:
        raise RuntimeError(
            "Zernio profile did not return an _id."
        )

    user.zernio_profile_id = str(profile_id)
    db.commit()
    db.refresh(user)

    return user.zernio_profile_id


def sync_user_facebook_connections(
    db: Session,
    user: User,
) -> list[FacebookConnection]:
    profile_id = ensure_user_zernio_profile(db, user)

    accounts = list_facebook_accounts(
        profile_id=profile_id,
    )

    now = _utc_now_naive()
    seen_account_ids: set[str] = set()

    for account in accounts:
        account_id = account.get("_id")

        if not account_id:
            continue

        account_id = str(account_id)
        seen_account_ids.add(account_id)

        remote_profile = account.get("profileId") or {}

        if isinstance(remote_profile, dict):
            remote_profile_id = remote_profile.get("_id")
        else:
            remote_profile_id = remote_profile

        connection = db.scalar(
            select(FacebookConnection).where(
                FacebookConnection.zernio_account_id
                == account_id
            )
        )

        if connection is None:
            connection = FacebookConnection(
                user_id=user.id,
                zernio_account_id=account_id,
            )
            db.add(connection)

        connection.user_id = user.id
        connection.zernio_profile_id = (
            str(remote_profile_id)
            if remote_profile_id
            else profile_id
        )
        connection.username = (
            account.get("username") or ""
        )
        connection.display_name = (
            account.get("displayName")
            or account.get("username")
            or "Facebook Page"
        )
        connection.profile_picture_url = (
            account.get("profilePicture")
        )
        connection.is_active = bool(
            account.get("isActive", True)
        )
        connection.last_error = None
        connection.last_verified_at = now

    local_connections = db.scalars(
        select(FacebookConnection).where(
            FacebookConnection.user_id == user.id,
            FacebookConnection.zernio_profile_id
            == profile_id,
        )
    ).all()

    for connection in local_connections:
        if (
            connection.zernio_account_id
            not in seen_account_ids
        ):
            connection.is_active = False

    db.commit()

    return list(
        db.scalars(
            select(FacebookConnection)
            .where(
                FacebookConnection.user_id == user.id,
                FacebookConnection.is_active.is_(True),
            )
            .order_by(FacebookConnection.display_name)
        ).all()
    )
