import hashlib
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimitOperation(StrEnum):
    """Expensive API operations with independent request budgets."""

    LICHESS_IMPORT = "lichess_import"
    PGN_UPLOAD = "pgn_upload"
    ANALYSIS = "analysis"
    REPORT = "report"


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of one atomic fixed-window quota consumption."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RateLimitUnavailableError(RuntimeError):
    """Raised when Redis cannot enforce an expensive-operation quota."""


class _RedisClient(Protocol):
    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object: ...

    async def ping(self) -> object: ...


_FIXED_WINDOW_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

_LIMIT_SETTING_NAMES = {
    RateLimitOperation.LICHESS_IMPORT: "MVP_LICHESS_IMPORTS_PER_WINDOW",
    RateLimitOperation.PGN_UPLOAD: "MVP_UPLOADS_PER_WINDOW",
    RateLimitOperation.ANALYSIS: "MVP_ANALYSIS_REQUESTS_PER_WINDOW",
    RateLimitOperation.REPORT: "MVP_REPORT_REQUESTS_PER_WINDOW",
}

_redis_client: Redis | None = None


def _get_redis_client() -> Redis:
    global _redis_client

    if not settings.redis_url:
        raise RateLimitUnavailableError("Redis URL is not configured")
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


def _key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _operation_limit(operation: RateLimitOperation) -> int:
    return int(getattr(settings, _LIMIT_SETTING_NAMES[operation]))


async def consume_operation_quota(
    operation: RateLimitOperation,
    *,
    redis_client: _RedisClient | None = None,
    now: float | None = None,
) -> RateLimitResult:
    """Atomically consume one fixed-window request quota from Redis."""
    window_seconds = settings.MVP_RATE_LIMIT_WINDOW_SECONDS
    current_second = int(time.time() if now is None else now)
    window_bucket = current_second // window_seconds
    retry_after = window_seconds - (current_second % window_seconds)
    limit = _operation_limit(operation)
    fingerprint = _key_fingerprint(settings.MVP_API_KEY.get_secret_value())
    redis_key = f"mvp-rate:{fingerprint}:{operation.value}:{window_bucket}"

    try:
        client = redis_client or _get_redis_client()
        count = int(
            await client.eval(
                _FIXED_WINDOW_SCRIPT,
                1,
                redis_key,
                retry_after,
            )
        )
    except RateLimitUnavailableError:
        logger.error(
            "rate_limit: operation=%s status=unavailable key=%s",
            operation.value,
            fingerprint,
        )
        raise
    except Exception as exc:
        logger.error(
            "rate_limit: operation=%s status=unavailable key=%s",
            operation.value,
            fingerprint,
        )
        raise RateLimitUnavailableError("Redis rate-limit backend is unavailable") from exc

    allowed = count <= limit
    logger.log(
        logging.INFO if allowed else logging.WARNING,
        "rate_limit: operation=%s status=%s key=%s",
        operation.value,
        "allowed" if allowed else "rejected",
        fingerprint,
    )
    return RateLimitResult(
        allowed=allowed,
        limit=limit,
        remaining=max(limit - count, 0),
        retry_after=retry_after,
    )


async def is_rate_limit_backend_ready(
    *,
    redis_client: _RedisClient | None = None,
) -> bool:
    """Return whether Redis is reachable for enforcing operation quotas."""
    try:
        client = redis_client or _get_redis_client()
        return bool(await client.ping())
    except Exception:
        return False
