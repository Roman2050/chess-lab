import asyncio
import logging
import re
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.config import settings
from app.models.enums import StandardPerfType
from app.services.lichess_errors import (
    LichessBusyError,
    LichessConfigurationError,
    LichessCoordinationError,
    LichessError,
    LichessProtocolError,
    LichessRateLimitedError,
    LichessUnavailableError,
    LichessUserNotFoundError,
)
from app.services.lichess_gate import lichess_request_gate


logger = logging.getLogger(__name__)

LICHESS_GAMES_URL = "https://lichess.org/api/games/user"
PGN_MEDIA_TYPES = frozenset({"application/x-chess-pgn", "text/plain"})
PGN_TAG_PAIR_PATTERN = re.compile(
    r'^\[[A-Za-z0-9_]+\s+"(?:[^"\\]|\\.)*"\]\s*$'
)
RESPONSE_CHUNK_BYTES = 64 * 1024


@dataclass(slots=True)
class _ResponseMetadata:
    upstream_http_status: int | None = None
    normalized_content_type: str | None = None
    declared_body_bytes: int | None = None
    factual_body_bytes: int | None = None


def _request_headers() -> dict[str, str]:
    headers = {
        "User-Agent": settings.LICHESS_USER_AGENT,
        "Accept": "application/x-chess-pgn",
    }
    if settings.LICHESS_API_TOKEN is not None:
        headers["Authorization"] = (
            f"Bearer {settings.LICHESS_API_TOKEN.get_secret_value()}"
        )
    return headers


def _raise_for_status(status_code: int) -> None:
    match status_code:
        case 200:
            return
        case 401 | 403:
            raise LichessConfigurationError
        case 404:
            raise LichessUserNotFoundError
        case 408:
            raise LichessUnavailableError
        case code if 500 <= code <= 599:
            raise LichessUnavailableError
        case _:
            raise LichessProtocolError


def _validate_content_length(response: httpx.Response, limit: int) -> int | None:
    content_length = response.headers.get("Content-Length")
    if content_length is None:
        return None

    normalized_length = content_length.strip()
    if re.fullmatch(r"[0-9]+", normalized_length, flags=re.ASCII) is None:
        raise LichessProtocolError
    try:
        declared_size = int(normalized_length)
    except ValueError:
        raise LichessProtocolError from None
    if declared_size > limit:
        raise LichessProtocolError
    return declared_size


def _media_type(response: httpx.Response) -> str | None:
    content_type = response.headers.get("Content-Type")
    if content_type is None:
        return None
    return content_type.split(";", maxsplit=1)[0].strip().casefold()


async def _read_bounded_body(
    response: httpx.Response,
    limit: int,
    metadata: _ResponseMetadata,
) -> bytes:
    chunks: list[bytes] = []
    size = 0
    metadata.factual_body_bytes = size
    chunk_size = min(RESPONSE_CHUNK_BYTES, limit + 1)
    async for chunk in response.aiter_bytes(chunk_size=chunk_size):
        size += len(chunk)
        metadata.factual_body_bytes = size
        if size > limit:
            raise LichessProtocolError
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_and_validate_pgn(body: bytes, media_type: str | None) -> str:
    if media_type is not None and media_type not in PGN_MEDIA_TYPES:
        raise LichessProtocolError

    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise LichessProtocolError from None

    normalized_body = text.strip().casefold()
    if normalized_body.startswith(("<!doctype html", "<html")):
        raise LichessProtocolError

    if media_type is None and text:
        first_nonempty_line = next(
            (line.strip() for line in text.splitlines() if line.strip()),
            None,
        )
        if first_nonempty_line is None:
            return text
        if PGN_TAG_PAIR_PATTERN.fullmatch(first_nonempty_line) is None:
            raise LichessProtocolError

    return text


async def fetch_games_from_lichess(
    username: str,
    max_games: int = 50,
    perf_type: StandardPerfType | None = None,
) -> str:
    """Fetch a bounded PGN export from Lichess."""
    operation_id = uuid4().hex
    started_at = perf_counter()
    perf_type_label = perf_type.value if perf_type else "all-standard"
    common_log_fields: dict[str, object] = {
        "operation_id": operation_id,
        "username_normalized": username.strip().casefold(),
        "max_games": max_games,
        "perf_type": perf_type_label,
    }
    logger.info(
        "lichess.request.started",
        extra={**common_log_fields, "status": "started"},
    )

    url = f"{LICHESS_GAMES_URL}/{quote(username, safe='')}"
    params: dict[str, int | str] = {
        "max": max_games,
        "tags": "true",
        "clocks": "false",
        "evals": "false",
        "opening": "true",
        "perfType": (
            perf_type.value
            if perf_type
            else "ultraBullet,bullet,blitz,rapid,classical,correspondence"
        ),
    }
    metadata = _ResponseMetadata()
    outcome = "failed"
    retry_after: int | None = None
    rate_limit_source: str | None = None
    failure_kind: str | None = "unexpected"

    try:
        async with lichess_request_gate() as gate:
            pgn_text: str | None = None
            upstream_rate_limited = False
            upstream_retry_after: str | None = None
            async with asyncio.timeout(settings.LICHESS_TOTAL_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    async with client.stream(
                        "GET",
                        url,
                        params=params,
                        headers=_request_headers(),
                    ) as response:
                        metadata.upstream_http_status = response.status_code
                        metadata.normalized_content_type = _media_type(response)
                        if response.status_code == 429:
                            upstream_rate_limited = True
                            upstream_retry_after = response.headers.get(
                                "Retry-After"
                            )
                        else:
                            _raise_for_status(response.status_code)
                            metadata.declared_body_bytes = _validate_content_length(
                                response,
                                settings.LICHESS_MAX_RESPONSE_BYTES,
                            )
                            body = await _read_bounded_body(
                                response,
                                settings.LICHESS_MAX_RESPONSE_BYTES,
                                metadata,
                            )
                            pgn_text = _decode_and_validate_pgn(
                                body,
                                metadata.normalized_content_type,
                            )

            if upstream_rate_limited:
                rate_limit_source = "upstream"
                retry_after = await gate.activate_cooldown(upstream_retry_after)
                raise LichessRateLimitedError(retry_after=retry_after)

        if pgn_text is None:
            raise LichessProtocolError
        outcome = "succeeded"
        failure_kind = None
        return pgn_text
    except LichessBusyError:
        outcome = "busy"
        failure_kind = None
        raise
    except LichessRateLimitedError as exc:
        outcome = "rate_limited"
        retry_after = exc.retry_after
        rate_limit_source = rate_limit_source or "local_cooldown"
        failure_kind = None
        raise
    except LichessCoordinationError:
        failure_kind = "redis"
        raise
    except LichessConfigurationError:
        failure_kind = "configuration"
        raise
    except LichessUserNotFoundError:
        failure_kind = "not_found"
        raise
    except LichessUnavailableError:
        failure_kind = (
            "upstream"
            if metadata.upstream_http_status is not None
            else "network"
        )
        raise
    except LichessProtocolError:
        failure_kind = "protocol"
        raise
    except httpx.DecodingError:
        failure_kind = "protocol"
        raise LichessProtocolError from None
    except (TimeoutError, httpx.TimeoutException):
        failure_kind = "timeout"
        raise LichessUnavailableError from None
    except httpx.RequestError:
        failure_kind = "network"
        raise LichessUnavailableError from None
    except asyncio.CancelledError:
        failure_kind = "cancelled"
        raise
    except LichessError:
        raise
    finally:
        duration_ms = max(
            round((perf_counter() - started_at) * 1000, 3),
            0.0,
        )
        terminal_fields: dict[str, object] = {
            **common_log_fields,
            "status": outcome,
            "upstream_http_status": metadata.upstream_http_status,
            "retry_after": retry_after if outcome == "rate_limited" else None,
            "duration_ms": duration_ms,
            "normalized_content_type": metadata.normalized_content_type,
            "declared_body_bytes": metadata.declared_body_bytes,
            "factual_body_bytes": metadata.factual_body_bytes,
        }
        if outcome == "rate_limited":
            terminal_fields["rate_limit_source"] = rate_limit_source
        elif outcome == "failed":
            terminal_fields["failure_kind"] = failure_kind

        logger.log(
            logging.INFO if outcome == "succeeded" else logging.WARNING,
            f"lichess.request.{outcome}",
            extra=terminal_fields,
        )
