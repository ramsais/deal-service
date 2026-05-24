from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Top-level environment selection and service metadata
    ENV: str = Field(default="dev", description="Current runtime environment: dev (default) or local")
    SERVICE_NAME: str = Field(default="deal-service")
    SERVICE_VERSION: str = Field(default="1.0.0")

    # OpenTelemetry configuration
    OTEL_SERVICE_NAME: str | None = Field(default=None)
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = Field(default=None)

    # Logging
    LOG_LEVEL: str = Field(default="INFO")

    # Storage
    STORAGE_FILE_PATH: str = Field(default="app/storage/deals.json")

    # External service config
    COMPANY_SERVICE_URL: str | None = Field(default=None)
    COMPANY_SERVICE_TIMEOUT: float = Field(default=30.0,
                                           description="HTTP timeout in seconds for company-service calls")
    COMPANY_SERVICE_RETRY_MAX: int = Field(default=3, description="Max retry attempts for company-service calls")
    COMPANY_SERVICE_RETRY_BACKOFF_MULTIPLIER: float = Field(default=1.0,
                                                            description="Tenacity exponential backoff multiplier")
    COMPANY_SERVICE_RETRY_BACKOFF_MIN: float = Field(default=0.5, description="Minimum backoff seconds")
    COMPANY_SERVICE_RETRY_BACKOFF_MAX: float = Field(default=5.0, description="Maximum backoff seconds")
    COMPANY_SERVICE_BREAKER_MAX_FAILURES: int = Field(default=5,
                                                      description="Number of consecutive failures to open the circuit")
    COMPANY_SERVICE_BREAKER_RESET_TIMEOUT: float = Field(default=60.0,
                                                         description="Seconds before attempting half-open")

    # Security / flags
    INTERNAL_API_KEY: str | None = Field(default=None)
    LOCAL_DEV: bool = Field(default=False, description="Disable auth checks for local development")

    # Server config
    APP_HOST: str = Field(default="0.0.0.0")
    APP_PORT: int = Field(default=9000)

    # Pydantic settings config
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")


def _select_env_file() -> str:
    """
    Resolve which .env file to load based on ENV (or APP_ENV) environment variable.
    Default is dev -> app/env/.env.dev
    If ENV=local (or APP_ENV=local) -> app/env/.env.local
    """
    env = os.getenv("ENV", os.getenv("APP_ENV", "dev")).lower()
    file_name = ".env.local" if env == "local" else ".env.dev"
    base_dir = Path(__file__).resolve().parent.parent / "env"
    return str(base_dir / file_name)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env_file = _select_env_file()
    return Settings(_env_file=env_file)


# Backwards-compatible singleton import: from app.services.config import settings
settings = get_settings()
