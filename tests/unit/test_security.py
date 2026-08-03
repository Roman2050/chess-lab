from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, status

import app.security as security
from app.config import settings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_and_invalid_api_keys_have_the_same_response() -> None:
    errors: list[HTTPException] = []

    for api_key in (None, "invalid-key"):
        with pytest.raises(HTTPException) as exc_info:
            await security.require_mvp_api_key(api_key)
        errors.append(exc_info.value)

    assert [(error.status_code, error.detail) for error in errors] == [
        (status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key"),
        (status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key"),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_valid_api_key_uses_constant_time_comparison(monkeypatch) -> None:
    api_key = settings.MVP_API_KEY.get_secret_value()
    compare_digest = MagicMock(return_value=True)
    monkeypatch.setattr(security.secrets, "compare_digest", compare_digest)

    assert await security.require_mvp_api_key(api_key) is None
    compare_digest.assert_called_once_with(api_key, api_key)
