from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.database import get_async_db
from app.services.rate_limit import (
    RateLimitOperation,
    RateLimitResult,
    RateLimitUnavailableError,
)


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exhausted_quota_returns_429_before_business_logic(
    async_client,
    auth_headers,
    monkeypatch,
) -> None:
    import app.routers.games as games_router
    import app.security as security

    consume = AsyncMock(
        return_value=RateLimitResult(
            allowed=False,
            limit=5,
            remaining=0,
            retry_after=37,
        )
    )
    fetch = AsyncMock(side_effect=AssertionError("Lichess must not be called"))
    monkeypatch.setattr(security, "consume_operation_quota", consume)
    monkeypatch.setattr(games_router, "fetch_games_from_lichess", fetch)

    response = await async_client.post(
        "/games/lichess/operator?max_games=1",
        headers=auth_headers,
    )

    assert response.status_code == 429
    assert response.json() == {"detail": "Operation rate limit exceeded"}
    assert response.headers["Retry-After"] == "37"
    consume.assert_awaited_once_with(RateLimitOperation.LICHESS_IMPORT)
    fetch.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_failure_returns_503_before_business_logic(
    async_client,
    auth_headers,
    monkeypatch,
) -> None:
    import app.routers.report as report_router
    import app.security as security

    consume = AsyncMock(side_effect=RateLimitUnavailableError("unavailable"))
    count_games = AsyncMock(
        side_effect=AssertionError("Report service must not be called")
    )
    monkeypatch.setattr(security, "consume_operation_quota", consume)
    monkeypatch.setattr(report_router, "count_analyzed_games", count_games)

    response = await async_client.post("/report/operator", headers=auth_headers)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Operation quota service is unavailable, try again later"
    }
    consume.assert_awaited_once_with(RateLimitOperation.REPORT)
    count_games.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_and_batch_analysis_share_one_operation_budget(
    async_client,
    auth_headers,
    override_db,
    monkeypatch,
) -> None:
    import app.routers.analysis as analysis_router
    import app.routers.games as games_router
    import app.security as security

    consume = AsyncMock(
        return_value=RateLimitResult(
            allowed=True,
            limit=20,
            remaining=19,
            retry_after=60,
        )
    )
    delay = MagicMock()
    monkeypatch.setattr(security, "consume_operation_quota", consume)
    monkeypatch.setattr(games_router.analyze_game, "delay", delay)
    monkeypatch.setattr(
        analysis_router,
        "get_unanalyzed_game_ids",
        AsyncMock(return_value=[2]),
    )

    single = await async_client.post("/games/1/analyze", headers=auth_headers)
    batch = await async_client.post(
        "/analyze/player/operator",
        headers=auth_headers,
    )

    assert single.status_code == 200
    assert batch.status_code == 200
    assert consume.await_args_list == [
        call(RateLimitOperation.ANALYSIS),
        call(RateLimitOperation.ANALYSIS),
    ]
    assert [queued.args for queued in delay.call_args_list] == [(1,), (2,)]
