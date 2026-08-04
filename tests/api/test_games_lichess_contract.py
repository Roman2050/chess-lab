import httpx
import pytest

from app.database import get_async_db
from app.services.lichess import (
    LichessBusyError,
    LichessConfigurationError,
    LichessProtocolError,
    LichessRateLimitedError,
    LichessUnavailableError,
    LichessUserNotFoundError,
)


@pytest.fixture
def override_db(app):
    async def _override():
        yield object()

    app.dependency_overrides[get_async_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def api_client(app, override_db, auth_headers):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=auth_headers,
    ) as client:
        yield client


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_error", "status_code", "detail"),
    [
        (
            LichessBusyError(),
            409,
            "Lichess import is already in progress",
        ),
        (
            LichessRateLimitedError(retry_after=None),
            429,
            "Lichess rate limit is active, retry later",
        ),
        (
            LichessUserNotFoundError("<html>upstream not-found page</html>"),
            404,
            "Lichess user not found",
        ),
        (
            LichessConfigurationError("Authorization: Bearer secret-token"),
            503,
            "Lichess integration is unavailable",
        ),
        (
            LichessUnavailableError("network exception details"),
            503,
            "Lichess is temporarily unavailable",
        ),
        (
            LichessProtocolError("<html>sensitive upstream response</html>"),
            502,
            "Invalid response from Lichess",
        ),
    ],
)
async def test_lichess_route_maps_service_errors_without_leaking_upstream(
    api_client,
    monkeypatch,
    service_error: Exception,
    status_code: int,
    detail: str,
):
    import app.routers.games as games_router

    async def fake_fetch(*_args, **_kwargs):
        raise service_error

    def should_not_be_called(*_args, **_kwargs):
        raise AssertionError("parse/bulk should not be called on fetch error")

    monkeypatch.setattr(games_router, "fetch_games_from_lichess", fake_fetch)
    monkeypatch.setattr(games_router, "parse_pgn_text", should_not_be_called)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    resp = await api_client.post("/games/lichess/u?max_games=1")
    assert resp.status_code == status_code
    assert resp.json() == {"detail": detail}
    assert "<html>" not in resp.text
    assert "secret-token" not in resp.text
    assert "network exception details" not in resp.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lichess_route_429_does_not_require_retry_after_in_chat_one(
    api_client,
    monkeypatch,
):
    import app.routers.games as games_router

    async def fake_fetch(*_args, **_kwargs):
        raise LichessRateLimitedError(retry_after=None)

    def should_not_be_called(*_args, **_kwargs):
        raise AssertionError("parse/bulk should not be called on fetch error")

    monkeypatch.setattr(games_router, "fetch_games_from_lichess", fake_fetch)
    monkeypatch.setattr(games_router, "parse_pgn_text", should_not_be_called)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    resp = await api_client.post("/games/lichess/u?max_games=1")
    assert resp.status_code == 429
    assert resp.json() == {
        "detail": "Lichess rate limit is active, retry later"
    }
    assert "Retry-After" not in resp.headers


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lichess_route_happy_path_returns_stats(api_client, monkeypatch):
    import app.routers.games as games_router

    async def fake_fetch(username: str, max_games: int, perf_type):
        assert username == "u"
        assert max_games == 2
        assert perf_type is None
        return "PGN TEXT"

    def fake_parse(_raw_pgn: str):
        return [
            {
                "unique_id": "u1",
                "white_player": "A",
                "black_player": "B",
                "result": "1-0",
                "winner": "White",
                "opening_name": None,
                "time_control": None,
                "pgn_content": "1. e4 e5 1-0",
            }
        ]

    async def fake_bulk_save(_db, games_data):
        assert len(games_data) == 1
        return {"saved_new": 1, "total_processed": 1}

    monkeypatch.setattr(games_router, "fetch_games_from_lichess", fake_fetch)
    monkeypatch.setattr(games_router, "parse_pgn_text", fake_parse)
    monkeypatch.setattr(games_router, "bulk_save_games", fake_bulk_save)

    resp = await api_client.post("/games/lichess/u?max_games=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Games from Lichess have been successfully processed for u"
    assert body["stats"]["saved_new"] == 1
    assert body["stats"]["total_processed"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lichess_route_returns_no_standard_games_when_parser_empty(api_client, monkeypatch):
    import app.routers.games as games_router

    async def fake_fetch(*_args, **_kwargs):
        return "PGN TEXT"

    def fake_parse(_raw_pgn: str):
        return []

    async def should_not_be_called(*_args, **_kwargs):
        raise AssertionError("bulk_save_games should not be called when parser returns empty")

    monkeypatch.setattr(games_router, "fetch_games_from_lichess", fake_fetch)
    monkeypatch.setattr(games_router, "parse_pgn_text", fake_parse)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    resp = await api_client.post("/games/lichess/u?max_games=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "No standard games found for the user u."
    assert body["stats"]["saved_new"] == 0
    assert body["stats"]["total_processed"] == 0

