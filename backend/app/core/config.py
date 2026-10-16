"""Application settings, loaded from the environment.

Secrets are never hardcoded and never logged. `.env` is git-ignored; the key
names live in `.env.example` with empty values.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- database -----------------------------------------------------------
    database_url: str

    # Supabase's connection pooler runs pgbouncer in transaction mode, which is
    # incompatible with asyncpg's prepared-statement cache. Set this to 0 when
    # connecting through the pooler (port 6543); the direct connection on port
    # 5432 and a local Postgres both work with the default.
    db_statement_cache_size: int = 100
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_command_timeout_seconds: float = 10.0

    # --- LLM ----------------------------------------------------------------
    # Optional on purpose: the application must start, serve the board and
    # report health without an AI provider configured. Analysis is the only
    # feature that requires it.
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 1
    llm_max_tool_calls: int = 4
    llm_max_iterations: int = 3
    # Opus 5.5 defaults to medium and Sonnet 5.5 to high, so it is always
    # set explicitly rather than inherited from whichever model is chosen.
    llm_effort: str = "medium"
    llm_max_output_tokens: int = 4096

    # --- analysis recovery --------------------------------------------------
    # A pending analysis whose worker started but never finished.
    stale_analysis_started_seconds: int = 180
    # A pending analysis whose worker never started at all (the process died
    # between inserting the row and picking up the background task).
    stale_analysis_never_started_seconds: int = 60

    # --- app ----------------------------------------------------------------
    app_env: str = "local"
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ai_provider_configured(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
