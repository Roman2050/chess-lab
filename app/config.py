from typing import Annotated, Self

from pydantic import (
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


GENERIC_HTTP_USER_AGENTS = (
    "aiohttp",
    "curl",
    "httpie",
    "postmanruntime",
    "python-httpx",
    "python-requests",
    "wget",
)


class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    redis_url: str | None = None

    # Required for every mutating or expensive API operation. There is no
    # development fallback: missing configuration must fail at import/startup.
    MVP_API_KEY: SecretStr = Field(min_length=32, repr=False)

    # Per-request operation budgets for the single-operator MVP.
    MAX_ANALYSIS_TASKS_PER_REQUEST: int = Field(default=10, ge=1)
    MAX_UPLOAD_GAMES: int = Field(default=100, ge=1)

    # Fixed-window Redis quotas for expensive operator POST requests.
    MVP_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=3600, ge=1)
    MVP_LICHESS_IMPORTS_PER_WINDOW: int = Field(default=5, ge=1)
    MVP_UPLOADS_PER_WINDOW: int = Field(default=10, ge=1)
    MVP_ANALYSIS_REQUESTS_PER_WINDOW: int = Field(default=20, ge=1)
    MVP_REPORT_REQUESTS_PER_WINDOW: int = Field(default=5, ge=1)

    # Lichess requires a stable application identity with a real contact. The
    # endpoint is always enabled in the MVP, so a generic HTTP-client identity
    # is a startup configuration error rather than a runtime fallback.
    LICHESS_USER_AGENT: str
    LICHESS_API_TOKEN: SecretStr | None = None
    LICHESS_TOTAL_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    LICHESS_MAX_RESPONSE_BYTES: int = Field(default=5 * 1024 * 1024, ge=1)

    # Stockfish — path is optional (analysis tasks no-op when missing); the
    # tuning knobs have engine-sane defaults. Ranges are sanity bounds, not
    # Stockfish's hard limits (it accepts much wider values).
    STOCKFISH_PATH: str | None = None
    STOCKFISH_DEPTH: int = Field(default=20, ge=1, le=40)
    STOCKFISH_MULTIPV: int = Field(default=2, ge=1, le=10)
    STOCKFISH_THREADS: int = Field(default=1, ge=1, le=128)
    STOCKFISH_HASH_MB: int = Field(default=128, ge=1, le=16384)

    # LLM / report (Phase 5) — any OpenAI-compatible endpoint works; switch
    # models by changing these. Defaults target a local Ollama instance.
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "llama3.1"
    LLM_API_KEY: str | None = None
    LLM_TEMPERATURE: float = Field(default=0.4, ge=0.0, le=2.0)
    LLM_TIMEOUT: int = Field(default=120, ge=1, le=600)
    REPORT_LANGUAGE: str = "en"
    REPORT_ALLOWED_LANGUAGES: Annotated[tuple[str, ...], NoDecode] = ("en", "uk")
    REPORT_REFRESH_THRESHOLD: int = Field(default=20, ge=1)

    # How long a report may stay `generating` before another request is allowed
    # to reclaim it: a worker killed mid-task leaves nothing behind that could
    # move the row on. Keep it well above LLM_TIMEOUT — reclaiming a generation
    # that is still alive costs a duplicate LLM call.
    REPORT_GENERATION_LEASE_SECONDS: int = Field(default=900, ge=1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        hide_input_in_errors=True,
    )

    @field_validator("LICHESS_USER_AGENT")
    @classmethod
    def validate_lichess_user_agent(cls, value: str) -> str:
        """Require a non-generic Lichess application identity."""
        user_agent = value.strip()
        if not user_agent:
            raise ValueError("LICHESS_USER_AGENT must not be blank")
        if "\r" in value or "\n" in value:
            raise ValueError("LICHESS_USER_AGENT must not contain CR or LF")

        product = (
            user_agent.casefold().split("/", maxsplit=1)[0].split(maxsplit=1)[0]
        )
        if product in GENERIC_HTTP_USER_AGENTS:
            raise ValueError("LICHESS_USER_AGENT must identify the application")
        return user_agent

    @field_validator("LICHESS_API_TOKEN", mode="before")
    @classmethod
    def normalize_blank_lichess_api_token(cls, value: object) -> object:
        """Treat a blank optional Lichess token as absent."""
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @field_validator("REPORT_ALLOWED_LANGUAGES", mode="before")
    @classmethod
    def parse_report_allowed_languages(cls, value: object) -> tuple[str, ...]:
        """Parse the comma-separated report-language allowlist once."""
        raw_items: object
        if isinstance(value, str):
            raw_items = value.split(",")
        else:
            raw_items = value

        if not isinstance(raw_items, (list, tuple, set, frozenset)):
            raise ValueError("REPORT_ALLOWED_LANGUAGES must be comma-separated")
        if not all(isinstance(item, str) for item in raw_items):
            raise ValueError("REPORT_ALLOWED_LANGUAGES must contain strings")

        languages = tuple(item.strip() for item in raw_items)
        if not languages or any(not language for language in languages):
            raise ValueError("REPORT_ALLOWED_LANGUAGES must not contain empty items")
        return languages

    @model_validator(mode="after")
    def validate_default_report_language(self) -> Self:
        """Ensure the default report language is allowed."""
        if self.REPORT_LANGUAGE not in self.REPORT_ALLOWED_LANGUAGES:
            raise ValueError(
                "REPORT_LANGUAGE must be included in REPORT_ALLOWED_LANGUAGES"
            )
        return self

    @computed_field
    @property
    def database_url_async(self) -> str:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        ).render_as_string(hide_password=False)
    
    @computed_field
    @property
    def database_url_sync(self) -> str:
        return URL.create(
            drivername="postgresql+psycopg2",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        ).render_as_string(hide_password=False)
    
settings = Settings()
