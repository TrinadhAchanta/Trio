"""Application settings, loaded from environment variables (and an optional .env file).

Secrets (provider API keys, JWT secret) live only on the server. Nothing here is ever
sent to the browser.
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Trio"
    environment: str = "development"  # development | test | production

    database_url: str = "postgresql+psycopg://trio:trio@localhost:5432/trio"
    # Optional PostgreSQL schema for all Trio tables, so Trio can share a database with another app.
    db_schema: str = Field(default="", pattern=r"^([a-z_][a-z0-9_]{0,62})?$")

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        # Hosting platforms (e.g. Render) hand out postgres:// or postgresql:// URLs; use the psycopg 3 driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

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
    # "local" = files on disk under storage_dir; "db" = file bytes in PostgreSQL (for hosts without a persistent disk)
    storage_backend: str = "local"
    max_upload_mb: int = 20

    # Limits
    rate_limit_auth_per_minute: int = 10
    rate_limit_chat_per_minute: int = 30
    max_output_tokens: int = 4096
    # Hard per-user daily spend guard in USD (estimated from provider pricing). Plans can set
    # their own lower/higher value; this is the fallback.
    daily_cost_limit_usd: float = 5.0
    # Safety cap on estimated AI spend across ALL users in the last 24 hours (0 disables it).
    site_daily_cost_limit_usd: float = 10.0

    # Billing. Plans, prices and credit rules live in a JSON file so they can change without code.
    plans_file: str = "plans.json"
    public_app_url: str = "http://localhost:3000"  # used for Stripe redirect URLs
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Safety: live-mode keys (sk_live_...) are refused unless this is explicitly true.
    stripe_allow_live: bool = False

    # Web search for deep research (either one). Without a key, a clearly labeled demo search is used
    # when the mock provider is enabled.
    brave_search_api_key: str = ""
    tavily_api_key: str = ""
    research_max_sources: int = 8


def db_connect_args(s: "Settings") -> dict:
    return {"options": f"-csearch_path={s.db_schema}"} if s.db_schema else {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
