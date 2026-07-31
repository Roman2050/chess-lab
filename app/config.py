from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    redis_url: str | None = None

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
    REPORT_REFRESH_THRESHOLD: int = Field(default=20, ge=1)

    # How long a report may stay `generating` before another request is allowed
    # to reclaim it: a worker killed mid-task leaves nothing behind that could
    # move the row on. Keep it well above LLM_TIMEOUT — reclaiming a generation
    # that is still alive costs a duplicate LLM call.
    REPORT_GENERATION_LEASE_SECONDS: int = Field(default=900, ge=1)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

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