import pytest

from app.config import Settings


@pytest.mark.unit
def test_db_url_with_special_chars_password() -> None:
    settings = Settings(
        DB_HOST="localhost",
        DB_PORT=5432,
        DB_USER="user",
        DB_PASSWORD="p@ssword#with$special%chars&",
        DB_NAME="chess_db",
    )

    async_url = settings.database_url_async
    sync_url = settings.database_url_sync

    # Ensure passwords with special characters are correctly encoded in URL strings
    assert "p%40ssword%23with%24special%25chars%26" in async_url
    assert "postgresql+asyncpg://" in async_url
    assert "localhost:5432/chess_db" in async_url

    assert "p%40ssword%23with%24special%25chars%26" in sync_url
    assert "postgresql+psycopg2://" in sync_url
    assert "localhost:5432/chess_db" in sync_url
