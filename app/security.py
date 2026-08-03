import secrets
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings


mvp_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    scheme_name="MVPApiKey",
    description="Single-operator key required for write operations.",
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
