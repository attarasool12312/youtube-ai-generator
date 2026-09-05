from pydantic_settings import BaseSettings, SettingsConfigDict
from app.paths import ENV_FILE


class Settings(BaseSettings):
    app_name: str
    app_url: str
    secret_key: str
    settings_encryption_key: str

    admin_username: str
    admin_email: str
    initial_admin_password: str = ""

    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool = True

    database_url: str
    redis_url: str
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:30b"
    session_days: int = 7
    default_duration_minutes: int = 10
    default_language: str = "English"
    default_voice: str = "male"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
