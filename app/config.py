from pydantic_settings import BaseSettings
from pydantic import computed_field


class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    # Optional settings that may exist in local `.env` during development.
    # Keeping them here prevents pydantic-settings from failing on unknown keys.
    redis_url: str | None = None
    stockfish_path: str | None = None

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
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8" 

settings = Settings()   