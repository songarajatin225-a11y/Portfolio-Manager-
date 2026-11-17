"""Application configuration loaded from environment variables.

Secrets (API keys, DB passwords) are only ever read from the environment or from the
encrypted ``provider_configs`` table - never hard-coded.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULT_SECRET = "dev-insecure-secret-change-me-0123456789abcdef"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "MarketIntel"
    log_level: str = "INFO"

    # --- Security -----------------------------------------------------------------------
    secret_key: str = _INSECURE_DEFAULT_SECRET
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt provider credentials at rest.
    secret_encryption_key: str | None = None
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    ws_ticket_ttl_seconds: int = 60
    cookie_secure: bool | None = None  # defaults to True in production
    cookie_domain: str | None = None
    allow_public_registration: bool = False
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    login_rate_limit_per_minute: int = 10

    # --- Infrastructure -----------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/marketintel"
    database_pool_size: int = 10
    redis_url: str | None = "redis://localhost:6379/0"
    frontend_origin: str = "http://localhost:3000"
    extra_cors_origins: list[str] = Field(default_factory=list)
    expose_api_docs: bool | None = None  # defaults to False in production

    # --- Runtime behaviour --------------------------------------------------------------
    run_worker_in_api: bool = False
    # Simulated feed is for development and automated tests only. Never enable in production
    # unless you explicitly want a demo instance (the UI shows a permanent SIMULATED banner).
    allow_simulated_feed: bool = False
    market_timezone: str = "Asia/Kolkata"

    # --- AI -------------------------------------------------------------------------------
    anthropic_api_key: str | None = None
    ai_default_model: str = "claude-opus-5-5"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.is_production

    @property
    def docs_enabled(self) -> bool:
        return self.expose_api_docs if self.expose_api_docs is not None else not self.is_production

    @property
    def cors_origins(self) -> list[str]:
        return [self.frontend_origin, *self.extra_cors_origins]

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.is_production:
            if self.secret_key == _INSECURE_DEFAULT_SECRET or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be set to a random value of at least 32 characters")
            if not self.secret_encryption_key:
                raise ValueError("SECRET_ENCRYPTION_KEY (a Fernet key) is required in production")
            if not self.run_worker_in_api and not self.redis_url:
                raise ValueError("REDIS_URL is required when the worker runs as a separate process")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
