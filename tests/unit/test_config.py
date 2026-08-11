import pytest
from pydantic import SecretStr, ValidationError

from app.config import (
    REPORT_LLM_MAX_RETRIES,
    REPORT_LLM_RETRY_BACKOFF_SECONDS,
    Settings,
)
from app.config import (
    settings as application_settings,
)

BASE_SETTINGS = {
    "DB_HOST": "localhost",
    "DB_PORT": 5432,
    "DB_USER": "user",
    "DB_PASSWORD": "p@ssword#with$special%chars&",
    "DB_NAME": "chess_db",
    "LICHESS_USER_AGENT": "ChessLabUnitTests/0.1 (+https://example.invalid/contact)",
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


@pytest.mark.unit
def test_runtime_logging_defaults_are_development_safe() -> None:
    settings = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        _env_file=None,
    )

    assert settings.APP_ENVIRONMENT == "development"
    assert settings.LOG_LEVEL == "INFO"
    assert settings.LOG_SERVICE == "chess-lab"


@pytest.mark.unit
def test_runtime_logging_rejects_unknown_environment_or_level() -> None:
    with pytest.raises(ValidationError):
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            APP_ENVIRONMENT="staging",
            _env_file=None,
        )

    with pytest.raises(ValidationError):
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            LOG_LEVEL="TRACE",
            _env_file=None,
        )


@pytest.mark.unit
def test_lichess_user_agent_is_required(monkeypatch) -> None:
    monkeypatch.delenv("LICHESS_USER_AGENT", raising=False)
    values = {key: value for key, value in BASE_SETTINGS.items() if key != "LICHESS_USER_AGENT"}

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            **values,
            MVP_API_KEY=VALID_MVP_API_KEY,
            _env_file=None,
        )

    error = exc_info.value.errors(include_input=False)[0]
    assert error["loc"] == ("LICHESS_USER_AGENT",)
    assert error["type"] == "missing"


@pytest.mark.unit
@pytest.mark.parametrize(
    "user_agent",
    [
        "",
        "   ",
        "ChessLab/0.1\rInjected: value",
        "ChessLab/0.1\nInjected: value",
        "python-httpx/0.28.1",
        "curl/8.14.1",
        "Wget/1.25.0",
        "python-requests/2.32.0",
    ],
)
def test_lichess_user_agent_rejects_unsafe_or_generic_values(user_agent: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            **{**BASE_SETTINGS, "LICHESS_USER_AGENT": user_agent},
            MVP_API_KEY=VALID_MVP_API_KEY,
            _env_file=None,
        )


@pytest.mark.unit
def test_lichess_user_agent_is_available_to_application_client() -> None:
    user_agent = "ChessLab/0.1 (+mailto:operator@example.com)"

    settings = Settings(
        **{**BASE_SETTINGS, "LICHESS_USER_AGENT": user_agent},
        MVP_API_KEY=VALID_MVP_API_KEY,
        _env_file=None,
    )

    assert settings.LICHESS_USER_AGENT == user_agent


@pytest.mark.unit
def test_test_suite_sets_deterministic_application_user_agent_before_import() -> None:
    assert application_settings.LICHESS_USER_AGENT == (
        "ChessLabTest/0.1 (+https://example.invalid/contact)"
    )


@pytest.mark.unit
@pytest.mark.parametrize("token", ["", "   ", SecretStr("")])
def test_blank_lichess_api_token_is_absent(token: str | SecretStr) -> None:
    settings = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        LICHESS_API_TOKEN=token,
        _env_file=None,
    )

    assert settings.LICHESS_API_TOKEN is None


@pytest.mark.unit
def test_lichess_api_token_has_secret_representation() -> None:
    token = "unit-test-lichess-secret"
    settings = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        LICHESS_API_TOKEN=token,
        _env_file=None,
    )

    assert settings.LICHESS_API_TOKEN is not None
    assert settings.LICHESS_API_TOKEN.get_secret_value() == token
    assert token not in repr(settings)


@pytest.mark.unit
@pytest.mark.parametrize(
    "field_name",
    [
        "MAX_ANALYSIS_TASKS_PER_REQUEST",
        "MAX_UPLOAD_GAMES",
        "MVP_RATE_LIMIT_WINDOW_SECONDS",
        "MVP_LICHESS_IMPORTS_PER_WINDOW",
        "MVP_UPLOADS_PER_WINDOW",
        "MVP_ANALYSIS_REQUESTS_PER_WINDOW",
        "MVP_REPORT_REQUESTS_PER_WINDOW",
        "LICHESS_TOTAL_TIMEOUT_SECONDS",
        "LICHESS_MIN_COOLDOWN_SECONDS",
        "LICHESS_MAX_COOLDOWN_SECONDS",
        "LICHESS_MAX_RESPONSE_BYTES",
    ],
)
def test_budgets_and_lichess_bounds_must_be_positive(field_name) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            **{field_name: 0},
            _env_file=None,
        )

    assert exc_info.value.errors(include_input=False)[0]["loc"] == (field_name,)


@pytest.mark.unit
def test_lichess_min_cooldown_must_not_exceed_maximum() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            LICHESS_MIN_COOLDOWN_SECONDS=61,
            LICHESS_MAX_COOLDOWN_SECONDS=60,
            _env_file=None,
        )


@pytest.mark.unit
def test_report_allowed_languages_are_trimmed_and_parsed_once() -> None:
    settings = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        REPORT_LANGUAGE="uk",
        REPORT_ALLOWED_LANGUAGES=" en, uk ",
        _env_file=None,
    )

    assert settings.REPORT_ALLOWED_LANGUAGES == ("en", "uk")


@pytest.mark.unit
def test_cors_origins_are_parsed_once_and_may_be_empty_locally() -> None:
    configured = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        CORS_ALLOWED_ORIGINS=" https://app.example, http://localhost:5173 ",
        _env_file=None,
    )
    local = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        CORS_ALLOWED_ORIGINS="",
        _env_file=None,
    )

    assert configured.CORS_ALLOWED_ORIGINS == (
        "https://app.example",
        "http://localhost:5173",
    )
    assert local.CORS_ALLOWED_ORIGINS == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    "origin",
    ["*", "https://app.example/", "https://app.example/path", "app.example"],
)
def test_cors_origins_must_be_exact_http_origins(origin: str) -> None:
    with pytest.raises(ValidationError, match="exact http"):
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            CORS_ALLOWED_ORIGINS=origin,
            _env_file=None,
        )


@pytest.mark.unit
def test_frontend_deployment_requires_a_cors_origin() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            FRONTEND_DEPLOYMENT_ENABLED=True,
            CORS_ALLOWED_ORIGINS="",
            _env_file=None,
        )


@pytest.mark.unit
@pytest.mark.parametrize("allowed_languages", ["", "en,,uk", "en, "])
def test_report_allowed_languages_reject_empty_items(allowed_languages) -> None:
    with pytest.raises(ValidationError, match="must not contain empty items"):
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            REPORT_ALLOWED_LANGUAGES=allowed_languages,
            _env_file=None,
        )


@pytest.mark.unit
def test_report_language_must_be_in_case_sensitive_allowed_set() -> None:
    with pytest.raises(ValidationError, match="must be included"):
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            REPORT_LANGUAGE="UK",
            REPORT_ALLOWED_LANGUAGES="en,uk",
            _env_file=None,
        )


@pytest.mark.unit
def test_report_lease_covers_all_llm_attempts_and_backoff() -> None:
    llm_timeout = 120
    minimum_lease = (REPORT_LLM_MAX_RETRIES + 1) * llm_timeout + REPORT_LLM_RETRY_BACKOFF_SECONDS

    with pytest.raises(ValidationError, match=f"minimum {minimum_lease}"):
        Settings(
            **BASE_SETTINGS,
            MVP_API_KEY=VALID_MVP_API_KEY,
            LLM_TIMEOUT=llm_timeout,
            REPORT_GENERATION_LEASE_SECONDS=minimum_lease - 1,
            _env_file=None,
        )

    settings = Settings(
        **BASE_SETTINGS,
        MVP_API_KEY=VALID_MVP_API_KEY,
        LLM_TIMEOUT=llm_timeout,
        REPORT_GENERATION_LEASE_SECONDS=minimum_lease,
        _env_file=None,
    )
    assert settings.REPORT_GENERATION_LEASE_SECONDS == minimum_lease
