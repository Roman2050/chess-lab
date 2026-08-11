import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.logging_config import JsonLogFormatter


class _JsonCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict] = []
        self.setFormatter(
            JsonLogFormatter(
                service="api",
                environment="production",
                app_version="0.1.0",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_endpoint(async_client):
    handler = _JsonCapture()
    api_logger = logging.getLogger("app.main")
    api_logger.addHandler(handler)
    try:
        resp = await async_client.get("/health")
    finally:
        api_logger.removeHandler(handler)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert handler.payloads == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_api_lifecycle_is_correlatable_and_omits_query_and_headers(async_client) -> None:
    handler = _JsonCapture()
    api_logger = logging.getLogger("app.main")
    api_logger.addHandler(handler)
    try:
        response = await async_client.get(
            "/api/v1/demo?private_query=must-not-appear",
            headers={"X-API-Key": "must-not-appear"},
        )
    finally:
        api_logger.removeHandler(handler)

    assert response.status_code == 200
    assert len(handler.payloads) == 1
    payload = handler.payloads[0]
    assert payload["event"] == "api.request.completed"
    assert payload["operation_id"]
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/v1/demo"
    assert payload["http_status"] == 200
    assert payload["status"] == "succeeded"
    assert payload["duration_ms"] >= 0
    assert "must-not-appear" not in json.dumps(payload)


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
