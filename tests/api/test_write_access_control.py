from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import httpx
import pytest

from app.database import get_async_db
from app.services.rate_limit import RateLimitOperation


PROTECTED_POSTS = [
    pytest.param("/api/v1/games/lichess/operator?max_games=1", False, id="lichess-import"),
    pytest.param("/api/v1/games/upload", True, id="pgn-upload"),
    pytest.param("/api/v1/games/1/analyze", False, id="single-analysis"),
    pytest.param("/api/v1/analyze/player/operator", False, id="batch-analysis"),
    pytest.param("/api/v1/report/operator", False, id="report"),
]
INVALID_API_KEY = "invalid-mvp-api-key-must-never-be-logged"


class _FakeResult:
    def scalar_one_or_none(self):
        return SimpleNamespace(is_analyzed=False)


class _FakeDb:
    async def execute(self, _statement):
        return _FakeResult()


@pytest.fixture
def override_db(app):
    async def _override():
        yield _FakeDb()

    app.dependency_overrides[get_async_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def access_client(app, override_db):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def blocked_collaborators(monkeypatch):
    import app.routers.analysis as analysis_router
    import app.routers.games as games_router
    import app.routers.report as report_router

    blocked = [
        MagicMock(side_effect=AssertionError("Lichess service must not be called")),
        MagicMock(side_effect=AssertionError("PGN parser must not be called")),
        MagicMock(side_effect=AssertionError("DB writer must not be called")),
        MagicMock(side_effect=AssertionError("analysis query must not be called")),
        MagicMock(side_effect=AssertionError("Celery must not be called")),
        MagicMock(side_effect=AssertionError("report service must not be called")),
    ]
    monkeypatch.setattr(games_router, "fetch_games_from_lichess", blocked[0])
    monkeypatch.setattr(games_router, "parse_pgn_text", blocked[1])
    monkeypatch.setattr(games_router, "bulk_save_games", blocked[2])
    monkeypatch.setattr(analysis_router, "get_unanalyzed_game_ids", blocked[3])
    monkeypatch.setattr(analysis_router.analyze_game, "delay", blocked[4])
    monkeypatch.setattr(report_router, "count_analyzed_games", blocked[5])
    return blocked


async def _post(
    client: httpx.AsyncClient,
    path: str,
    *,
    upload: bool,
    api_key: str | None = None,
) -> httpx.Response:
    headers = {"X-API-Key": api_key} if api_key is not None else None
    files = (
        {"file": ("games.pgn", b"1. e4 e5 1/2-1/2", "application/x-chess-pgn")}
        if upload
        else None
    )
    return await client.post(path, headers=headers, files=files)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "upload"), PROTECTED_POSTS)
async def test_protected_post_rejects_missing_and_invalid_key_before_business_logic(
    access_client,
    allow_rate_limits,
    blocked_collaborators,
    caplog,
    path,
    upload,
):
    missing = await _post(access_client, path, upload=upload)
    invalid = await _post(
        access_client,
        path,
        upload=upload,
        api_key=INVALID_API_KEY,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json() == invalid.json() == {
        "detail": "Missing or invalid API key"
    }
    assert INVALID_API_KEY not in missing.text
    assert INVALID_API_KEY not in invalid.text
    assert INVALID_API_KEY not in caplog.text
    for collaborator in blocked_collaborators:
        collaborator.assert_not_called()
    allow_rate_limits.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_valid_key_reaches_each_existing_post_handler(
    access_client,
    auth_headers,
    allow_rate_limits,
    monkeypatch,
):
    import app.routers.analysis as analysis_router
    import app.routers.games as games_router
    import app.routers.report as report_router

    fetch = AsyncMock(return_value="PGN text")
    parse = MagicMock(return_value=[])
    delay = MagicMock()
    game_ids = AsyncMock(return_value=[2])
    report_count = AsyncMock(return_value=0)
    get_report = AsyncMock(return_value=None)
    generation_stale = AsyncMock(return_value=False)

    monkeypatch.setattr(games_router, "fetch_games_from_lichess", fetch)
    monkeypatch.setattr(games_router, "parse_pgn_text", parse)
    monkeypatch.setattr(games_router.analyze_game, "delay", delay)
    monkeypatch.setattr(analysis_router, "get_unanalyzed_game_ids", game_ids)
    monkeypatch.setattr(report_router, "count_analyzed_games", report_count)
    monkeypatch.setattr(report_router, "get_report", get_report)
    monkeypatch.setattr(report_router, "is_generation_stale", generation_stale)

    responses = [
        await access_client.post(
            "/api/v1/games/lichess/operator?max_games=1", headers=auth_headers
        ),
        await access_client.post(
            "/api/v1/games/upload",
            headers=auth_headers,
            files={"file": ("games.pgn", b"PGN text", "application/x-chess-pgn")},
        ),
        await access_client.post("/api/v1/games/1/analyze", headers=auth_headers),
        await access_client.post(
            "/api/v1/analyze/player/operator", headers=auth_headers
        ),
        await access_client.post("/api/v1/report/operator", headers=auth_headers),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200]
    fetch.assert_awaited_once()
    assert parse.call_count == 2
    game_ids.assert_awaited_once()
    assert [call.args for call in delay.call_args_list] == [(1,), (2,)]
    report_count.assert_awaited_once()
    assert allow_rate_limits.await_args_list == [
        call(RateLimitOperation.LICHESS_IMPORT),
        call(RateLimitOperation.PGN_UPLOAD),
        call(RateLimitOperation.ANALYSIS),
        call(RateLimitOperation.ANALYSIS),
        call(RateLimitOperation.REPORT),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openapi_marks_only_post_operations_as_protected(access_client):
    response = await access_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["components"]["securitySchemes"]["MVPApiKey"] == {
        "type": "apiKey",
        "description": "Single-operator key required for write operations.",
        "in": "header",
        "name": "X-API-Key",
    }

    protected_operations = {
        ("/api/v1/games/lichess/{username}", "post"),
        ("/api/v1/games/upload", "post"),
        ("/api/v1/games/{game_id}/analyze", "post"),
        ("/api/v1/analyze/player/{username}", "post"),
        ("/api/v1/report/{username}", "post"),
    }
    actual_post_operations = {
        (path, method)
        for path, operations in schema["paths"].items()
        for method in operations
        if method == "post"
    }
    assert actual_post_operations == protected_operations

    for path, method in protected_operations:
        assert schema["paths"][path][method]["security"] == [{"MVPApiKey": []}]

    for operations in schema["paths"].values():
        if "get" in operations:
            assert not operations["get"].get("security")

    assert (await access_client.get("/health")).status_code == 200
    assert (await access_client.get("/docs")).status_code == 200
