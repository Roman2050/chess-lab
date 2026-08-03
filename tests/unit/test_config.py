import pytest
from pydantic import ValidationError

from app.config import Settings


BASE_SETTINGS = {
    "DB_HOST": "localhost",
    "DB_PORT": 5432,
    "DB_USER": "user",
    "DB_PASSWORD": "p@ssword#with$special%chars&",
    "DB_NAME": "chess_db",
}
VALID_MVP_API_KEY = "unit-test-mvp-key-0123456789abcdef"


@pytest.mark.unit
def test_db_url_with_special_chars_password() -> None:
    settings = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        _env_file=None,
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


@pytest.mark.unit
def test_mvp_api_key_is_required(monkeypatch) -> None:
    monkeypatch.delenv("MVP_API_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(**BASE_SETTINGS, _env_file=None)

    error = exc_info.value.errors(include_input=False)[0]
    assert error["loc"] == ("MVP_API_KEY",)
    assert error["type"] == "missing"


@pytest.mark.unit
def test_mvp_api_key_rejects_short_value_without_leaking_it() -> None:
    short_key = "short-private-key"

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=short_key,
            _env_file=None,
        )

    assert exc_info.value.errors(include_input=False)[0]["type"] == "too_short"
    assert short_key not in str(exc_info.value)


@pytest.mark.unit
def test_mvp_api_key_is_hidden_from_settings_repr() -> None:
    settings = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        _env_file=None,
    )

    assert settings.MVP_API_KEY.get_secret_value() == VALID_MVP_API_KEY
    assert VALID_MVP_API_KEY not in repr(settings)
