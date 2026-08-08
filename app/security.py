import secrets
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings
from app.services.rate_limit import (
    RateLimitOperation,
    RateLimitUnavailableError,
    consume_operation_quota,
)


mvp_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    scheme_name="MVPApiKey",
    description=(
        "Server-side key for the single Chess Lab operator. It protects every write "
        "operation and is never issued to demo users or embedded in a browser client."
    ),
)


async def require_mvp_api_key(
    api_key: Annotated[str | None, Security(mvp_api_key_header)],
) -> None:
    """Require the configured single-operator API key."""
    expected_key = settings.MVP_API_KEY.get_secret_value()
    if api_key is None or not secrets.compare_digest(api_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )


async def _require_operation_quota(operation: RateLimitOperation) -> None:
    try:
        result = await consume_operation_quota(operation)
    except RateLimitUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operation quota service is unavailable, try again later",
        ) from exc

    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Operation rate limit exceeded",
            headers={"Retry-After": str(result.retry_after)},
        )


async def require_lichess_import_quota(
    _: Annotated[None, Security(require_mvp_api_key)],
) -> None:
    """Authenticate the operator and consume one Lichess-import request."""
    await _require_operation_quota(RateLimitOperation.LICHESS_IMPORT)


async def require_pgn_upload_quota(
    _: Annotated[None, Security(require_mvp_api_key)],
) -> None:
    """Authenticate the operator and consume one PGN-upload request."""
    await _require_operation_quota(RateLimitOperation.PGN_UPLOAD)


async def require_analysis_quota(
    _: Annotated[None, Security(require_mvp_api_key)],
) -> None:
    """Authenticate the operator and consume one shared analysis request."""
    await _require_operation_quota(RateLimitOperation.ANALYSIS)


async def require_report_quota(
    _: Annotated[None, Security(require_mvp_api_key)],
) -> None:
    """Authenticate the operator and consume one report request."""
    await _require_operation_quota(RateLimitOperation.REPORT)
