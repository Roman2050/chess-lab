import httpx
import pytest

from app.database import get_async_db


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
async def test_lichess_route_maps_http_status_error(api_client, monkeypatch):
    import app.routers.games as games_router

    async def fake_fetch(*_args, **_kwargs):
        request = httpx.Request("GET", "https://lichess.org/api/games/user/u")
        response = httpx.Response(429, text="Too Many Requests", request=request)
        raise httpx.HTTPStatusError("error", request=request, response=response)

    def should_not_be_called(*_args, **_kwargs):
        raise AssertionError("parse/bulk should not be called on fetch error")

    monkeypatch.setattr(games_router, "fetch_games_from_lichess", fake_fetch)
    monkeypatch.setattr(games_router, "parse_pgn_text", should_not_be_called)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    resp = await api_client.post("/games/lichess/u?max_games=1")
    assert resp.status_code == 429
    assert "Lichess API error:" in resp.json()["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lichess_route_maps_generic_exception_to_500(api_client, monkeypatch):
    import app.routers.games as games_router

    async def fake_fetch(*_args, **_kwargs):
        raise RuntimeError("network down")

    def should_not_be_called(*_args, **_kwargs):
        raise AssertionError("parse/bulk should not be called on fetch error")

    monkeypatch.setattr(games_router, "fetch_games_from_lichess", fake_fetch)
    monkeypatch.setattr(games_router, "parse_pgn_text", should_not_be_called)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    resp = await api_client.post("/games/lichess/u?max_games=1")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Unable to connect to Lichess API"


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

