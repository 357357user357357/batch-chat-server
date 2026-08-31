from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables (.env)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_password: str = "change-me-please"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    database_url: str = "sqlite:///./data/batch_chat.db"
    token_expire_days: int = 30
    port: int = 8000
    # Where the web UI is served from inside the container
    ui_dir: str = "app/static"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()