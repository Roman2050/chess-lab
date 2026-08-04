import os
from unittest.mock import AsyncMock

import httpx
import pytest


# IMPORTANT: The app config is loaded at import time (Settings()) and requires
# DB_* plus MVP_API_KEY. Tests use a dedicated non-production key and set all
# required values before importing any application module.
TEST_MVP_API_KEY = "test-mvp-api-key-0123456789abcdef"

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_USER", "chess")
os.environ.setdefault("DB_PASSWORD", "chess")
os.environ.setdefault("DB_NAME", "chess_lab")
os.environ["MVP_API_KEY"] = TEST_MVP_API_KEY


@pytest.fixture(autouse=True)
def allow_rate_limits(monkeypatch):
    """Keep unrelated tests isolated from the external Redis backend."""
    import app.security as security
    from app.services.rate_limit import RateLimitResult

    consume = AsyncMock(
        return_value=RateLimitResult(
            allowed=True,
            limit=100,
            remaining=99,
            retry_after=60,
        )
    )
    monkeypatch.setattr(security, "consume_operation_quota", consume)
    return consume


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture(scope="session")
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_MVP_API_KEY}


@pytest.fixture
async def async_client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def sample_pgn_text() -> str:
    return (
        '[Event "Rated Blitz game"]\n'
        '[Site "https://lichess.org/abcdef12"]\n'
        '[Date "2026.05.06"]\n'
        '[Round "-"]\n'
        '[White "WhitePlayer"]\n'
        '[Black "BlackPlayer"]\n'
        '[Result "1-0"]\n'
        '[Variant "Standard"]\n'
        "\n"
        "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 1-0\n"
    )
