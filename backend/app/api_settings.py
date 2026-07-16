from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import ApiSetting

cipher = Fernet(settings.settings_encryption_key.encode())


def encrypt_value(value):
    return cipher.encrypt(value.encode()).decode()


def save_api_key(name, value):
    if not value:
        return

    with SessionLocal() as db:
        item = db.scalar(
            select(ApiSetting).where(ApiSetting.name == name)
        )
        encrypted = encrypt_value(value)

        if item:
            item.encrypted_value = encrypted
        else:
            db.add(ApiSetting(name=name, encrypted_value=encrypted))
        db.commit()


def get_api_key(name):
    with SessionLocal() as db:
        item = db.scalar(
            select(ApiSetting).where(ApiSetting.name == name)
        )
        if not item:
            return ""
        return cipher.decrypt(
            item.encrypted_value.encode()
        ).decode()


def has_api_key(name):
    return bool(get_api_key(name))
