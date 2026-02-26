from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/tgmax"

    # Redis (broker only)
    REDIS_URL: str = "redis://redis:6379/0"

    # Encryption
    SECRET_KEY: str = "change-me-generate-fernet-key"
    SESSION_COOKIE_NAME: str = "session_key"
    SESSION_TTL_DAYS: int = 30
    SESSION_COOKIE_SECURE: bool = False

    # Telegram
    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_LOG_FULL_SEND_CODE_RESPONSE: bool = True
    # Device profile (some users report in-app code arrives only with these set)
    TELEGRAM_DEVICE_MODEL: str = "Desktop"
    TELEGRAM_SYSTEM_VERSION: str = "10"
    TELEGRAM_APP_VERSION: str = "4.16.0"
    TELEGRAM_LANG_CODE: str = "en"
    TELEGRAM_SYSTEM_LANG_CODE: str = "en-US"

    # Storage
    MEDIA_ROOT: str = "/data/media"

    # MAX API
    MAX_API_BASE: str = "https://platform-api.max.ru"
    MAX_ATTACHMENTS_PER_MESSAGE: int = 10

    # Rate limits
    PUBLISH_DELAY_MS_MIN: int = 300
    PUBLISH_DELAY_MS_MAX: int = 800
    UPLOAD_RETRY: int = 5
    SEND_RETRY: int = 5
    TG_IMPORT_BATCH_SIZE: int = 50
    TG_IMPORT_DELAY_MS: int = 100
    IMPORT_STALE_TTL_MINUTES: int = 60
    IMPORT_STALE_CHECK_SECONDS: int = 120

    # Worker (concurrency overridden in docker-compose via env)
    WORKER_CONCURRENCY: int = 2
    IMPORT_WORKER_CONCURRENCY: int = 4
    PUBLISH_WORKER_CONCURRENCY: int = 2

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
