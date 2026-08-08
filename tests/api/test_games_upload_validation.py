import os
import httpx
import pytest
from app.database import get_async_db


@pytest.fixture
def override_db(app):
    async def _override():
        # We don't hit the real DB in these tests.
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
async def test_upload_rejects_non_pgn_extension(api_client, monkeypatch):
    import app.routers.games as games_router

    def should_not_be_called(*args, **kwargs):
        raise AssertionError("parse/bulk should not be called for invalid extension")

    monkeypatch.setattr(games_router, "parse_pgn_text", should_not_be_called)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    files = {"file": ("games.txt", b"anything", "text/plain")}
    resp = await api_client.post("/games/upload", files=files)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Only files with the .pgn extension may be uploaded"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejects_non_utf8_file(api_client, monkeypatch):
    import app.routers.games as games_router

    def should_not_be_called(*args, **kwargs):
        raise AssertionError("parse/bulk should not be called for invalid encoding")

    monkeypatch.setattr(games_router, "parse_pgn_text", should_not_be_called)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    # Invalid UTF-8 bytes should trigger UnicodeDecodeError in the endpoint.
    files = {"file": ("games.pgn", b"\xff\xfe\x00\x00", "application/octet-stream")}
    resp = await api_client.post("/games/upload", files=files)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "File encoding error. UTF-8 is expected."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_returns_no_valid_games_when_parser_returns_empty(api_client, monkeypatch):
    import app.routers.games as games_router

    def fake_parse(_raw_pgn: str):
        return []

    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("bulk_save_games should not be called when no games parsed")

    monkeypatch.setattr(games_router, "parse_pgn_text", fake_parse)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    files = {"file": ("games.pgn", b"some utf-8 text", "application/octet-stream")}
    resp = await api_client.post("/games/upload", files=files)

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "No valid standard games were found in the file."
    assert body["stats"]["saved_new"] == 0
    assert body["stats"]["total_processed"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_happy_path_uses_parser_and_bulk_save(api_client, monkeypatch):
    import app.routers.games as games_router

    parsed_games = [
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

    def fake_parse(_raw_pgn: str):
        return parsed_games

    async def fake_bulk_save(db, games_data):
        assert games_data is parsed_games
        return {"saved_new": 1, "total_processed": len(games_data)}

    monkeypatch.setattr(games_router, "parse_pgn_text", fake_parse)
    monkeypatch.setattr(games_router, "bulk_save_games", fake_bulk_save)

    files = {"file": ("games.pgn", b"utf-8 pgn content", "application/octet-stream")}
    resp = await api_client.post("/games/upload", files=files)

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "File games.pgn successfully processed"
    assert body["stats"]["saved_new"] == 1
    assert body["stats"]["total_processed"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejects_more_than_game_limit_before_db_write(
    api_client,
    monkeypatch,
):
    import app.routers.games as games_router

    parsed_games = [{"unique_id": str(index)} for index in range(3)]

    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("bulk_save_games should not be called over the game limit")

    monkeypatch.setattr(games_router.settings, "MAX_UPLOAD_GAMES", 2)
    monkeypatch.setattr(games_router, "parse_pgn_text", lambda _raw_pgn: parsed_games)
    monkeypatch.setattr(games_router, "bulk_save_games", should_not_be_called)

    files = {"file": ("games.pgn", b"utf-8 pgn content", "application/octet-stream")}
    resp = await api_client.post("/games/upload", files=files)

    assert resp.status_code == 413
    assert resp.json()["detail"] == "PGN file exceeds the limit of 2 games"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_accepts_exact_game_limit(api_client, monkeypatch):
    import app.routers.games as games_router

    parsed_games = [{"unique_id": str(index)} for index in range(2)]

    async def fake_bulk_save(_db, games_data):
        assert games_data is parsed_games
        return {"saved_new": 2, "total_processed": 2}

    monkeypatch.setattr(games_router.settings, "MAX_UPLOAD_GAMES", 2)
    monkeypatch.setattr(games_router, "parse_pgn_text", lambda _raw_pgn: parsed_games)
    monkeypatch.setattr(games_router, "bulk_save_games", fake_bulk_save)

    files = {"file": ("games.pgn", b"utf-8 pgn content", "application/octet-stream")}
    resp = await api_client.post("/games/upload", files=files)

    assert resp.status_code == 200
    assert resp.json()["stats"] == {"saved_new": 2, "total_processed": 2}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_too_large_413(api_client, monkeypatch):
    import app.routers.games as games_router

    # Temporarily set MAX_UPLOAD_BYTES to 100 bytes for testing
    monkeypatch.setattr(games_router, "MAX_UPLOAD_BYTES", 100)

    large_content = b"x" * 150
    files = {"file": ("games.pgn", large_content, "application/octet-stream")}
    resp = await api_client.post("/games/upload", files=files)

    assert resp.status_code == 413
    assert "File size exceeds maximum limit" in resp.json()["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_without_filename_400(api_client):
    files = {"file": ("   ", b"some content", "application/octet-stream")}
    resp = await api_client.post("/games/upload", files=files)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Filename is required"


