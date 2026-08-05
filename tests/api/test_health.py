from unittest.mock import AsyncMock

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_endpoint(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ready_endpoint_reports_redis_status(async_client, monkeypatch):
    import app.main as main
    import app.services.lichess as lichess_service

    readiness = AsyncMock(side_effect=[True, False])
    lichess_fetch = AsyncMock()
    monkeypatch.setattr(main, "is_rate_limit_backend_ready", readiness)
    monkeypatch.setattr(
        lichess_service,
        "fetch_games_from_lichess",
        lichess_fetch,
    )

    ready = await async_client.get("/ready")
    unavailable = await async_client.get("/ready")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "redis": "ok"}
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "status": "unavailable",
        "redis": "unavailable",
    }
    assert (await async_client.get("/health")).status_code == 200
    assert readiness.await_count == 2
    lichess_fetch.assert_not_awaited()

