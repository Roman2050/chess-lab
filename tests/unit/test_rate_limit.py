import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services.rate_limit import (
    RateLimitOperation,
    RateLimitUnavailableError,
    consume_operation_quota,
    is_rate_limit_backend_ready,
)


class _AtomicFakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.eval_calls: list[tuple[str, int, str, int]] = []
        self._lock = asyncio.Lock()

    async def eval(
        self,
        script: str,
        numkeys: int,
        key: object,
        expiry: object,
    ) -> int:
        redis_key = str(key)
        ttl = int(expiry)
        async with self._lock:
            self.eval_calls.append((script, numkeys, redis_key, ttl))
            count = self.counts.get(redis_key, 0) + 1
            self.counts[redis_key] = count
            return count

    async def ping(self) -> bool:
        return True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fixed_window_limits_are_independent_and_reset(
    monkeypatch,
) -> None:
    redis = _AtomicFakeRedis()
    monkeypatch.setattr(settings, "MVP_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(settings, "MVP_LICHESS_IMPORTS_PER_WINDOW", 2)
    monkeypatch.setattr(settings, "MVP_UPLOADS_PER_WINDOW", 1)

    lichess_results = [
        await consume_operation_quota(
            RateLimitOperation.LICHESS_IMPORT,
            redis_client=redis,
            now=125,
        )
        for _ in range(3)
    ]
    upload_results = [
        await consume_operation_quota(
            RateLimitOperation.PGN_UPLOAD,
            redis_client=redis,
            now=125,
        )
        for _ in range(2)
    ]

    assert [result.allowed for result in lichess_results] == [True, True, False]
    assert [result.allowed for result in upload_results] == [True, False]
    assert {result.retry_after for result in lichess_results + upload_results} == {55}

    next_window = await consume_operation_quota(
        RateLimitOperation.LICHESS_IMPORT,
        redis_client=redis,
        now=180,
    )
    assert next_window.allowed is True
    assert next_window.retry_after == 60

    raw_api_key = settings.MVP_API_KEY.get_secret_value()
    assert all(raw_api_key not in key for _, _, key, _ in redis.eval_calls)
    assert all(numkeys == 1 for _, numkeys, _, _ in redis.eval_calls)
    assert all("INCR" in script and "EXPIRE" in script for script, _, _, _ in redis.eval_calls)
    assert all(1 <= ttl <= 60 for _, _, _, ttl in redis.eval_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_increments_do_not_exceed_the_limit(monkeypatch) -> None:
    redis = _AtomicFakeRedis()
    monkeypatch.setattr(settings, "MVP_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(settings, "MVP_ANALYSIS_REQUESTS_PER_WINDOW", 7)

    results = await asyncio.gather(
        *(
            consume_operation_quota(
                RateLimitOperation.ANALYSIS,
                redis_client=redis,
                now=125,
            )
            for _ in range(40)
        )
    )

    assert sum(result.allowed for result in results) == 7
    assert len(redis.eval_calls) == 40
    assert len(redis.counts) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_exception_fails_closed_without_logging_api_key(
    caplog,
) -> None:
    redis = AsyncMock()
    redis.eval.side_effect = ConnectionError("Redis unavailable")

    with pytest.raises(RateLimitUnavailableError):
        await consume_operation_quota(
            RateLimitOperation.REPORT,
            redis_client=redis,
            now=125,
        )

    assert settings.MVP_API_KEY.get_secret_value() not in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_backend_readiness_is_fail_closed(monkeypatch) -> None:
    healthy = AsyncMock()
    healthy.ping.return_value = True
    unavailable = AsyncMock()
    unavailable.ping.side_effect = ConnectionError("Redis unavailable")

    assert await is_rate_limit_backend_ready(redis_client=healthy) is True
    assert await is_rate_limit_backend_ready(redis_client=unavailable) is False

    monkeypatch.setattr(settings, "redis_url", None)
    assert await is_rate_limit_backend_ready() is False
