"""Application-owned configuration.

The application deliberately exposes Codex-specific settings rather than a
generic provider/model routing abstraction.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(value: str) -> str:
    """Use the installed psycopg v3 dialect for standard PostgreSQL URLs."""
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    codex_app_server_command: str = Field(
        default="codex app-server --stdio",
        validation_alias="CODEX_APP_SERVER_COMMAND",
    )
    codex_default_model: str | None = Field(
        default=None,
        validation_alias="CODEX_DEFAULT_MODEL",
    )
    codex_default_reasoning_effort: str | None = Field(
        default=None,
        validation_alias="CODEX_DEFAULT_REASONING_EFFORT",
    )
    codex_timeout_seconds: float = Field(
        default=180.0,
        validation_alias="CODEX_TIMEOUT_SECONDS",
        gt=0,
    )
    codex_working_directory: str | None = Field(
        default=None,
        validation_alias="CODEX_WORKING_DIRECTORY",
    )
    official_fpl_base_url: str = Field(
        default="https://fantasy.premierleague.com/api",
        validation_alias="OFFICIAL_FPL_BASE_URL",
    )
    official_fpl_timeout_seconds: float = Field(
        default=15.0,
        validation_alias="OFFICIAL_FPL_TIMEOUT_SECONDS",
        gt=0,
    )
    official_fpl_season_id: str | None = Field(
        default=None,
        validation_alias="OFFICIAL_FPL_SEASON_ID",
    )
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias="CORS_ORIGINS",
    )

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required for database-backed operations")
        return normalize_database_url(self.database_url)

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
