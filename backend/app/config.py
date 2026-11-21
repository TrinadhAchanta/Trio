"""Application settings, loaded from environment variables (and an optional .env file).

Secrets (provider API keys, JWT secret) live only on the server. Nothing here is ever
sent to the browser.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Trio"
    environment: str = "development"  # development | test | production

    database_url: str = "postgresql+psycopg://trio:trio@localhost:5432/trio"

    # Auth
    jwt_secret: str = Field(default="dev-only-change-me-dev-only-change-me", min_length=32)
    jwt_algorithm: str = "HS256"
    session_ttl_hours: int = 24 * 7
    cookie_name: str = "trio_session"
    cookie_secure: bool = False  # set true behind HTTPS in production

    # CORS (only needed if the frontend is served from another origin; the Next.js dev
    # server proxies /api so the browser sees a single origin).
    cors_origins: list[str] = ["http://localhost:3000"]

    # Provider keys. Empty -> provider is reported as "not configured".
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    xai_api_key: str = ""

    # Optional base-URL overrides (e.g. a gateway). Defaults are the official endpoints.
    # Deliberately NOT named *_BASE_URL so ambient SDK variables in a developer shell are not
    # picked up by accident.
    openai_api_base: str = "https://api.openai.com/v1"
    anthropic_api_base: str = "https://api.anthropic.com"
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    xai_api_base: str = "https://api.x.ai/v1"

    # The mock provider is for local demos and automated tests. It is always clearly labeled.
    enable_mock_provider: bool = True

    # Uploads
    storage_dir: str = "./data/uploads"
    max_upload_mb: int = 20

    # Limits
    rate_limit_auth_per_minute: int = 10
    rate_limit_chat_per_minute: int = 30
    max_output_tokens: int = 4096
    # Hard per-user daily spend guard in USD (estimated from provider pricing).
    daily_cost_limit_usd: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
