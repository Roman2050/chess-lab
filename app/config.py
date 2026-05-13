from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @computed_field
    @property
    def database_url_async(self) -> str:
        return (
            f"postgresql+asyncpg://"
            f"{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )
    
    @computed_field
    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql+psycopg2://"
            f"{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )
    
settings = Settings()   