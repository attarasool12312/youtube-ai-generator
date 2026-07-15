from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str
    app_url: str
    secret_key: str

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
    session_days: int = 7
    default_duration_minutes: int = 10
    default_language: str = "English"
    default_voice: str = "male"

    model_config = SettingsConfigDict(
        env_file="/workspace/youtube-ai-generator/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
