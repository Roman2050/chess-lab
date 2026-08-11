import math
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

from redis.asyncio import Redis

from app.config import settings
from app.services.lichess_errors import (
    LichessBusyError,
    LichessCoordinationError,
    LichessRateLimitedError,
)

LICHESS_REQUEST_LOCK_KEY = "chess-lab:lichess:request-lock"
LICHESS_COOLDOWN_KEY = "chess-lab:lichess:cooldown"
LOCK_SAFETY_MARGIN_SECONDS = 15

_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class _RedisClient(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> object: ...

    async def pttl(self, name: str) -> object: ...

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object: ...


_redis_client: Redis | None = None


def _get_redis_client() -> Redis:
    global _redis_client

    if not settings.redis_url:
        raise LichessCoordinationError("Redis URL is not configured")
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
) -> int:
    """Return a configured, bounded cooldown for a Retry-After value."""
    parsed_seconds = 0
    normalized = value.strip() if value is not None else ""

    if re.fullmatch(r"[0-9]+", normalized, flags=re.ASCII):
        try:
            parsed_seconds = int(normalized)
        except ValueError:
            parsed_seconds = 0
    elif normalized:
        try:
            retry_at = parsedate_to_datetime(normalized)
            if retry_at.tzinfo is not None:
                current_time = now or datetime.now(UTC)
                if current_time.tzinfo is None:
                    current_time = current_time.replace(tzinfo=UTC)
                parsed_seconds = max(
                    math.ceil((retry_at - current_time).total_seconds()),
                    0,
                )
        except (OverflowError, TypeError, ValueError):
            parsed_seconds = 0

    return min(
        max(parsed_seconds, settings.LICHESS_MIN_COOLDOWN_SECONDS),
        settings.LICHESS_MAX_COOLDOWN_SECONDS,
    )


async def _active_cooldown(
    redis_client: _RedisClient,
) -> int | None:
    try:
        ttl_ms = int(await redis_client.pttl(LICHESS_COOLDOWN_KEY))
    except Exception as exc:
        raise LichessCoordinationError("Redis Lichess coordination is unavailable") from exc

    if ttl_ms == -2:
        return None
    if ttl_ms == -1:
        raise LichessCoordinationError("Lichess cooldown key has no expiry")
    if ttl_ms > 0:
        return min(
            max(math.ceil(ttl_ms / 1000), 1),
            settings.LICHESS_MAX_COOLDOWN_SECONDS,
        )
    if ttl_ms == 0:
        return None
    raise LichessCoordinationError("Redis returned an invalid cooldown TTL")


async def _raise_if_cooling_down(redis_client: _RedisClient) -> None:
    retry_after = await _active_cooldown(redis_client)
    if retry_after is not None:
        raise LichessRateLimitedError(retry_after=retry_after)


def _lock_ttl_ms() -> int:
    return math.ceil((settings.LICHESS_TOTAL_TIMEOUT_SECONDS + LOCK_SAFETY_MARGIN_SECONDS) * 1000)


async def _acquire_lock(redis_client: _RedisClient, owner_token: str) -> None:
    try:
        acquired = await redis_client.set(
            LICHESS_REQUEST_LOCK_KEY,
            owner_token,
            nx=True,
            px=_lock_ttl_ms(),
        )
    except Exception as exc:
        raise LichessCoordinationError("Redis Lichess coordination is unavailable") from exc

    if not acquired:
        raise LichessBusyError


async def _release_lock(redis_client: _RedisClient, owner_token: str) -> None:
    try:
        await redis_client.eval(
            _RELEASE_LOCK_SCRIPT,
            1,
            LICHESS_REQUEST_LOCK_KEY,
            owner_token,
        )
    except Exception as exc:
        raise LichessCoordinationError("Redis Lichess coordination is unavailable") from exc


@dataclass(frozen=True, slots=True)
class LichessRequestGate:
    """An owned deployment-wide request lease with cooldown control."""

    _redis_client: _RedisClient

    async def activate_cooldown(
        self,
        retry_after: str | None,
        *,
        now: datetime | None = None,
    ) -> int:
        """Store the bounded upstream cooldown and return its duration."""
        wait_seconds = parse_retry_after(retry_after, now=now)
        try:
            stored = await self._redis_client.set(
                LICHESS_COOLDOWN_KEY,
                "1",
                ex=wait_seconds,
            )
        except Exception as exc:
            raise LichessCoordinationError("Redis Lichess coordination is unavailable") from exc
        if not stored:
            raise LichessCoordinationError("Redis rejected the Lichess cooldown")
        return wait_seconds


@asynccontextmanager
async def lichess_request_gate(
    *,
    redis_client: _RedisClient | None = None,
) -> AsyncIterator[LichessRequestGate]:
    """Acquire the deployment-wide Lichess lease without waiting."""
    client = redis_client or _get_redis_client()
    await _raise_if_cooling_down(client)

    owner_token = secrets.token_urlsafe(32)
    await _acquire_lock(client, owner_token)
    try:
        await _raise_if_cooling_down(client)
        yield LichessRequestGate(client)
    finally:
        await _release_lock(client, owner_token)
