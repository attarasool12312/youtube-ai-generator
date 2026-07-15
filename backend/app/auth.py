from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

serializer = URLSafeTimedSerializer(settings.secret_key)


def create_session_token(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id}, salt="admin-session")


def read_session_token(token: str) -> int | None:
    try:
        data = serializer.loads(
            token,
            salt="admin-session",
            max_age=settings.session_days * 86400,
        )
        return int(data["user_id"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None


def read_session_token(token: str) -> int | None:
    try:
        data = serializer.loads(
            token,
            salt="admin-session",
            max_age=settings.session_days * 86400,
        )
        return int(data["user_id"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None


def read_reset_token(token: str) -> int | None:
    try:
        data = serializer.loads(
            token,
            salt="password-reset",
            max_age=3600,
        )
        return int(data["user_id"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None
