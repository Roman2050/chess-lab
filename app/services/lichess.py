import asyncio
import re
from urllib.parse import quote

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


LICHESS_GAMES_URL = "https://lichess.org/api/games/user"
PGN_MEDIA_TYPES = frozenset({"application/x-chess-pgn", "text/plain"})
PGN_TAG_PAIR_PATTERN = re.compile(
    r'^\[[A-Za-z0-9_]+\s+"(?:[^"\\]|\\.)*"\]\s*$'
)
RESPONSE_CHUNK_BYTES = 64 * 1024


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


def _validate_content_length(response: httpx.Response, limit: int) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length is None:
        return

    normalized_length = content_length.strip()
    if re.fullmatch(r"[0-9]+", normalized_length, flags=re.ASCII) is None:
        raise LichessProtocolError
    if int(normalized_length) > limit:
        raise LichessProtocolError


def _media_type(response: httpx.Response) -> str | None:
    content_type = response.headers.get("Content-Type")
    if content_type is None:
        return None
    return content_type.split(";", maxsplit=1)[0].strip().casefold()


async def _read_bounded_body(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    chunk_size = min(RESPONSE_CHUNK_BYTES, limit + 1)
    async for chunk in response.aiter_bytes(chunk_size=chunk_size):
        size += len(chunk)
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

    try:
        async with lichess_request_gate() as gate:
            upstream_retry_after: str | None = None
            async with asyncio.timeout(settings.LICHESS_TOTAL_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    async with client.stream(
                        "GET",
                        url,
                        params=params,
                        headers=_request_headers(),
                    ) as response:
                        if response.status_code == 429:
                            upstream_retry_after = response.headers.get(
                                "Retry-After"
                            )
                        else:
                            _raise_for_status(response.status_code)
                            _validate_content_length(
                                response,
                                settings.LICHESS_MAX_RESPONSE_BYTES,
                            )
                            body = await _read_bounded_body(
                                response,
                                settings.LICHESS_MAX_RESPONSE_BYTES,
                            )
                            return _decode_and_validate_pgn(
                                body,
                                _media_type(response),
                            )

            retry_after = await gate.activate_cooldown(upstream_retry_after)
            raise LichessRateLimitedError(retry_after=retry_after)
    except LichessError:
        raise
    except httpx.DecodingError:
        raise LichessProtocolError from None
    except (TimeoutError, httpx.RequestError):
        raise LichessUnavailableError from None
