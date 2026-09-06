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

    # Google Vertex AI (direct calls, models prefixed "vertex:" in the UI)
    google_project_id: str = ""
    google_location: str = "us-central1"
    # Paste the whole service-account JSON key content here (single line/escaped)
    google_service_account_json: str = ""
    # ...or point to a mounted key file instead of pasting the JSON
    google_service_account_file: str = ""

    # AWS Bedrock (direct calls, models prefixed "bedrock:" in the UI)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_region: str = "us-east-1"

    # Tavily web search (server-side "web search" toggle in the chat)
    tavily_api_key: str = ""

    # Prompt-cache duration for OpenRouter/Anthropic requests, in seconds.
    # Anthropic only supports ~5 minutes ("ephemeral", no ttl) or up to 1 hour
    # ("1h"); anything >= 3600 is sent as "1h", otherwise the 5-minute default.
    cache_duration_seconds: int = 3600

    # Cache keep-alive: after the last real chat request, periodically send a
    # near-empty request that hits and refreshes the 1-hour prompt cache, so a
    # cache written at 1-hour price stays cheap for this many hours instead of
    # just one (0 = disabled). Pings go out every 45 minutes (< the 1h TTL).
    cache_keepalive_hours: int = 3

    # CORS origins allowed to call the API, comma-separated. "*" (default)
    # keeps phone/PC clients working over LAN IPs; tighten it (e.g.
    # "https://chat.example.com") when the server sits behind a real domain.
    cors_origins: str = "*"

    # Public base URL of this instance (used in confirmation links and OAuth
    # redirects). Falls back to the request host when empty.
    public_base_url: str = ""

    # SMTP for signup confirmation e-mails. Without SMTP configured the
    # e-mail registration endpoint is disabled (pairing codes still work).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True

    # Google OAuth (Sign in with Google). Create credentials at
    # https://console.cloud.google.com/apis/credentials (type "Web
    # application") and add "{public_base_url}/api/auth/google/callback" as
    # an authorized redirect URI. Empty = Google button disabled.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()