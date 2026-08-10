from typing import Annotated, Self
from urllib.parse import urlsplit

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

# Report generation makes one initial LLM call plus three retries. Celery's
# exponential backoff starts at one second, so the maximum non-jittered delay
# across those retries is 1 + 2 + 4 seconds (jitter can only shorten it).
REPORT_LLM_MAX_RETRIES = 3
REPORT_LLM_RETRY_BACKOFF_SECONDS = 1 + 2 + 4
API_V1_PREFIX = "/api/v1"


class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    redis_url: str | None = None

    # Public discovery and browser access. CORS origins are exact origins
    # (scheme + host + optional port), never URL prefixes or wildcard patterns.
    DEMO_PLAYER_NAME: str = Field(default="DemoPlayer", min_length=1)
    CORS_ALLOWED_ORIGINS: Annotated[tuple[str, ...], NoDecode] = ()
    FRONTEND_DEPLOYMENT_ENABLED: bool = False

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
    LICHESS_MIN_COOLDOWN_SECONDS: int = Field(default=60, ge=1)
    LICHESS_MAX_COOLDOWN_SECONDS: int = Field(default=3600, ge=1)
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
    # move the row on. It must cover the initial LLM call, three retries and
    # their backoff — reclaiming a live generation costs a duplicate LLM call.
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

        product = user_agent.casefold().split("/", maxsplit=1)[0].split(maxsplit=1)[0]
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

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value: object) -> tuple[str, ...]:
        """Parse and validate the exact browser-origin allowlist once."""
        if isinstance(value, str) and not value.strip():
            return ()
        raw_items: object = value.split(",") if isinstance(value, str) else value
        if not isinstance(raw_items, (list, tuple, set, frozenset)):
            raise ValueError("CORS_ALLOWED_ORIGINS must be comma-separated")

        origins = tuple(item.strip() for item in raw_items if isinstance(item, str))
        if len(origins) != len(raw_items):
            raise ValueError("CORS_ALLOWED_ORIGINS must contain strings")
        if len(set(origins)) != len(origins):
            raise ValueError("CORS_ALLOWED_ORIGINS must not contain duplicates")

        for origin in origins:
            parsed = urlsplit(origin)
            if (
                not origin
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("CORS_ALLOWED_ORIGINS entries must be exact http(s) origins")
        return origins

    @model_validator(mode="after")
    def validate_dependent_settings(self) -> Self:
        """Validate settings that depend on another setting."""
        if self.LICHESS_MIN_COOLDOWN_SECONDS > self.LICHESS_MAX_COOLDOWN_SECONDS:
            raise ValueError(
                "LICHESS_MIN_COOLDOWN_SECONDS must not exceed LICHESS_MAX_COOLDOWN_SECONDS"
            )
        if self.REPORT_LANGUAGE not in self.REPORT_ALLOWED_LANGUAGES:
            raise ValueError("REPORT_LANGUAGE must be included in REPORT_ALLOWED_LANGUAGES")
        if self.FRONTEND_DEPLOYMENT_ENABLED and not self.CORS_ALLOWED_ORIGINS:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must not be empty when frontend deployment is enabled"
            )
        minimum_report_lease = (
            REPORT_LLM_MAX_RETRIES + 1
        ) * self.LLM_TIMEOUT + REPORT_LLM_RETRY_BACKOFF_SECONDS
        if self.REPORT_GENERATION_LEASE_SECONDS < minimum_report_lease:
            raise ValueError(
                "REPORT_GENERATION_LEASE_SECONDS must cover all LLM attempts "
                f"and retry backoff (minimum {minimum_report_lease})"
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
