import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from app.config import settings
from app.services.lichess_errors import (
    LichessBusyError,
    LichessCoordinationError,
    LichessRateLimitedError,
)
from app.services.lichess_gate import (
    LICHESS_COOLDOWN_KEY,
    LICHESS_REQUEST_LOCK_KEY,
    LOCK_SAFETY_MARGIN_SECONDS,
    lichess_request_gate,
    parse_retry_after,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls_ms: dict[str, int] = {}
        self.events: list[tuple[object, ...]] = []
        self.pttl_results: list[int] = []

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        self.events.append(("set", name, value, ex, px, nx))
        if nx and name in self.values:
            return None
        self.values[name] = value
        if ex is not None:
            self.ttls_ms[name] = ex * 1000
        elif px is not None:
            self.ttls_ms[name] = px
        return True

    async def pttl(self, name: str) -> int:
        self.events.append(("pttl", name))
        if self.pttl_results:
            return self.pttl_results.pop(0)
        if name not in self.values:
            return -2
        return self.ttls_ms.get(name, -1)

    async def eval(
        self,
        _script: str,
        _numkeys: int,
        key: str,
        owner_token: str,
    ) -> int:
        self.events.append(("eval", key, owner_token))
        if self.values.get(key) != owner_token:
            return 0
        del self.values[key]
        self.ttls_ms.pop(key, None)
        return 1


class UnavailableRedis(FakeRedis):
    async def pttl(self, name: str) -> int:
        raise ConnectionError(f"Redis unavailable for {name}")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("120", 120),
        ("5", 60),
        ("999999", 3600),
        (None, 60),
        ("", 60),
        ("invalid", 60),
        ("-10", 60),
    ],
)
def test_retry_after_seconds_are_bounded(
    value: str | None,
    expected: int,
) -> None:
    assert parse_retry_after(value) == expected


@pytest.mark.unit
def test_retry_after_http_date_uses_aware_time_and_rounds_up() -> None:
    now = datetime(2026, 8, 4, 10, 0, 0, 500_000, tzinfo=UTC)
    retry_at = now.replace(microsecond=0) + timedelta(seconds=91)

    assert parse_retry_after(format_datetime(retry_at), now=now) == 91


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_request_acquires_lock_and_releases_it() -> None:
    redis = FakeRedis()

    async with lichess_request_gate(redis_client=redis):
        assert LICHESS_REQUEST_LOCK_KEY in redis.values

    assert LICHESS_REQUEST_LOCK_KEY not in redis.values


@pytest.mark.unit
@pytest.mark.asyncio
async def test_two_coroutines_allow_at_most_one_outbound_call() -> None:
    redis = FakeRedis()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    outbound_calls = 0

    async def outbound() -> None:
        nonlocal outbound_calls
        async with lichess_request_gate(redis_client=redis):
            outbound_calls += 1
            first_entered.set()
            await release_first.wait()

    first = asyncio.create_task(outbound())
    await first_entered.wait()

    with pytest.raises(LichessBusyError):
        await outbound()

    release_first.set()
    await first
    assert outbound_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_does_not_delete_another_owners_lock() -> None:
    redis = FakeRedis()
    context = lichess_request_gate(redis_client=redis)
    await context.__aenter__()
    redis.values[LICHESS_REQUEST_LOCK_KEY] = "new-owner"

    await context.__aexit__(None, None, None)

    assert redis.values[LICHESS_REQUEST_LOCK_KEY] == "new-owner"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancellation_releases_owned_lock() -> None:
    redis = FakeRedis()
    entered = asyncio.Event()
    never_set = asyncio.Event()

    async def wait_inside_gate() -> None:
        async with lichess_request_gate(redis_client=redis):
            entered.set()
            await never_set.wait()

    task = asyncio.create_task(wait_inside_gate())
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert LICHESS_REQUEST_LOCK_KEY not in redis.values


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lock_ttl_exceeds_total_timeout() -> None:
    redis = FakeRedis()

    async with lichess_request_gate(redis_client=redis):
        lock_ttl_ms = redis.ttls_ms[LICHESS_REQUEST_LOCK_KEY]
        assert lock_ttl_ms > settings.LICHESS_TOTAL_TIMEOUT_SECONDS * 1000
        assert lock_ttl_ms == (
            settings.LICHESS_TOTAL_TIMEOUT_SECONDS
            + LOCK_SAFETY_MARGIN_SECONDS
        ) * 1000


@pytest.mark.unit
@pytest.mark.asyncio
async def test_active_cooldown_blocks_before_lock_acquire() -> None:
    redis = FakeRedis()
    redis.values[LICHESS_COOLDOWN_KEY] = "1"
    redis.ttls_ms[LICHESS_COOLDOWN_KEY] = 12_001

    with pytest.raises(LichessRateLimitedError) as exc_info:
        async with lichess_request_gate(redis_client=redis):
            raise AssertionError("gate must not open during cooldown")

    assert exc_info.value.retry_after == 13
    assert not any(
        event[0] == "set" and event[1] == LICHESS_REQUEST_LOCK_KEY
        for event in redis.events
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_active_cooldown_retry_after_is_bounded() -> None:
    redis = FakeRedis()
    redis.values[LICHESS_COOLDOWN_KEY] = "1"
    redis.ttls_ms[LICHESS_COOLDOWN_KEY] = 9_999_999

    with pytest.raises(LichessRateLimitedError) as exc_info:
        async with lichess_request_gate(redis_client=redis):
            raise AssertionError("gate must not open during cooldown")

    assert exc_info.value.retry_after == settings.LICHESS_MAX_COOLDOWN_SECONDS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cooldown_is_stored_before_lock_release() -> None:
    redis = FakeRedis()

    async with lichess_request_gate(redis_client=redis) as gate:
        wait = await gate.activate_cooldown("75")

    assert wait == 75
    cooldown_set_index = next(
        index
        for index, event in enumerate(redis.events)
        if event[0] == "set" and event[1] == LICHESS_COOLDOWN_KEY
    )
    release_index = next(
        index for index, event in enumerate(redis.events) if event[0] == "eval"
    )
    assert cooldown_set_index < release_index
    assert redis.ttls_ms[LICHESS_COOLDOWN_KEY] == 75_000


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_lock_cooldown_check_closes_race_and_releases_lock() -> None:
    redis = FakeRedis()
    redis.pttl_results = [-2, 5_001]

    with pytest.raises(LichessRateLimitedError) as exc_info:
        async with lichess_request_gate(redis_client=redis):
            raise AssertionError("post-lock cooldown must keep the gate closed")

    assert exc_info.value.retry_after == 6
    assert LICHESS_REQUEST_LOCK_KEY not in redis.values


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_unavailable_fails_closed_before_lock() -> None:
    redis = UnavailableRedis()

    with pytest.raises(LichessCoordinationError):
        async with lichess_request_gate(redis_client=redis):
            raise AssertionError("gate must not open without Redis")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cooldown_key_without_expiry_is_coordination_failure() -> None:
    redis = FakeRedis()
    redis.values[LICHESS_COOLDOWN_KEY] = "1"

    with pytest.raises(LichessCoordinationError):
        async with lichess_request_gate(redis_client=redis):
            raise AssertionError("invalid cooldown must fail closed")
