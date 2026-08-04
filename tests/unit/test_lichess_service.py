import asyncio

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.config import settings
from app.models.enums import StandardPerfType
from app.services.lichess import (
    LichessConfigurationError,
    LichessProtocolError,
    LichessRateLimitedError,
    LichessUnavailableError,
    LichessUserNotFoundError,
    fetch_games_from_lichess,
)


USERNAME = "someuser"
URL = f"https://lichess.org/api/games/user/{USERNAME}"
PGN_TEXT = (
    '[Event "Rated Blitz game"]\n'
    '[Site "https://lichess.org/abcdef12"]\n'
    '[White "A"]\n'
    '[Black "B"]\n'
    '[Result "1-0"]\n\n'
    "1. e4 e5 1-0\n"
)


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay: float = 0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.chunks_yielded = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.chunks_yielded += 1
            yield chunk


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_sends_identity_accept_and_explicit_perf_query() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(URL).mock(
            return_value=httpx.Response(200, text=PGN_TEXT)
        )

        result = await fetch_games_from_lichess(
            username=USERNAME,
            max_games=37,
            perf_type=StandardPerfType.blitz,
        )

    assert result == PGN_TEXT
    request = route.calls[0].request
    assert request.headers["User-Agent"] == settings.LICHESS_USER_AGENT
    assert request.headers["Accept"] == "application/x-chess-pgn"
    assert "Authorization" not in request.headers
    assert dict(request.url.params.multi_items()) == {
        "max": "37",
        "tags": "true",
        "clocks": "false",
        "evals": "false",
        "opening": "true",
        "perfType": "blitz",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_preserves_default_perf_type_query() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(URL).mock(
            return_value=httpx.Response(200, text=PGN_TEXT)
        )

        await fetch_games_from_lichess(
            username=USERNAME,
            max_games=12,
            perf_type=None,
        )

    assert route.calls[0].request.url.params["perfType"] == (
        "ultraBullet,bullet,blitz,rapid,classical,correspondence"
    )
    assert route.calls[0].request.url.params["max"] == "12"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_url_encodes_username_path_segment() -> None:
    encoded_url = "https://lichess.org/api/games/user/name%2Fwith%20space"
    with respx.mock(assert_all_called=True) as router:
        route = router.get(encoded_url).mock(
            return_value=httpx.Response(200, content=b"")
        )

        assert await fetch_games_from_lichess("name/with space") == ""

    assert route.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_sends_optional_bearer_token_without_logging_it(
    monkeypatch,
    caplog,
) -> None:
    token = "lichess-unit-test-secret"
    monkeypatch.setattr(settings, "LICHESS_API_TOKEN", SecretStr(token))

    with respx.mock(assert_all_called=True) as router:
        route = router.get(URL).mock(
            return_value=httpx.Response(200, text=PGN_TEXT)
        )
        await fetch_games_from_lichess(USERNAME)

    assert route.calls[0].request.headers["Authorization"] == f"Bearer {token}"
    assert token not in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_network_error_does_not_expose_bearer_token(monkeypatch, caplog) -> None:
    token = "lichess-network-error-secret"
    monkeypatch.setattr(settings, "LICHESS_API_TOKEN", SecretStr(token))

    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            side_effect=httpx.ConnectError(f"connection failed: {token}")
        )

        with pytest.raises(LichessUnavailableError) as exc_info:
            await fetch_games_from_lichess(USERNAME)

    assert token not in str(exc_info.value)
    assert token not in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    [
        "application/x-chess-pgn",
        "application/x-chess-pgn; charset=utf-8",
        "text/plain",
        "text/plain; Charset=UTF-8",
    ],
)
async def test_allowed_pgn_media_types_return_body_unchanged(content_type: str) -> None:
    body = PGN_TEXT.encode()
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": content_type},
                content=body,
            )
        )

        result = await fetch_games_from_lichess(USERNAME)

    assert result == PGN_TEXT


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Content-Type": "text/plain"}])
async def test_empty_success_body_is_allowed(headers: dict[str, str]) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(200, headers=headers, content=b"")
        )

        assert await fetch_games_from_lichess(USERNAME) == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_content_type_accepts_pgn_like_body() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(200, content=PGN_TEXT.encode())
        )

        assert await fetch_games_from_lichess(USERNAME) == PGN_TEXT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_content_type_rejects_non_pgn_body() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(200, content=b"service says hello")
        )

        with pytest.raises(LichessProtocolError):
            await fetch_games_from_lichess(USERNAME)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    [
        "text/html",
        "application/xhtml+xml",
        "application/json",
        "application/problem+json",
        "application/xml",
        "text/xml",
        "application/problem+xml",
        "application/octet-stream",
    ],
)
async def test_unsupported_media_type_is_protocol_error(content_type: str) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": content_type},
                content=PGN_TEXT.encode(),
            )
        )

        with pytest.raises(LichessProtocolError):
            await fetch_games_from_lichess(USERNAME)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("html", ["<!DOCTYPE html><p>blocked", "  <HTML>blocked"])
async def test_html_signature_is_rejected_even_with_allowed_media_type(
    html: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                content=html.encode(),
            )
        )

        with pytest.raises(LichessProtocolError):
            await fetch_games_from_lichess(USERNAME)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_utf8_is_protocol_error() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "application/x-chess-pgn"},
                content=b"\xff\xfe",
            )
        )

        with pytest.raises(LichessProtocolError):
            await fetch_games_from_lichess(USERNAME)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", ["invalid", "-1", "6"])
async def test_invalid_or_oversized_content_length_fails_before_body_read(
    content_length: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "LICHESS_MAX_RESPONSE_BYTES", 5)
    stream = CountingStream([b"never read"])
    response = httpx.Response(
        200,
        headers={
            "Content-Type": "text/plain",
            "Content-Length": content_length,
        },
        stream=stream,
    )

    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(return_value=response)
        with pytest.raises(LichessProtocolError):
            await fetch_games_from_lichess(USERNAME)

    assert stream.chunks_yielded == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_actual_body_exactly_at_limit_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LICHESS_MAX_RESPONSE_BYTES", 5)
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                content=b"12345",
            )
        )

        assert await fetch_games_from_lichess(USERNAME) == "12345"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_actual_body_over_limit_stops_reading(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LICHESS_MAX_RESPONSE_BYTES", 5)
    stream = CountingStream([b"123", b"456", b"must-not-be-read"])
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/plain"},
        stream=stream,
    )

    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(return_value=response)
        with pytest.raises(LichessProtocolError):
            await fetch_games_from_lichess(USERNAME)

    assert stream.chunks_yielded == 2


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (201, LichessProtocolError),
        (302, LichessProtocolError),
        (400, LichessProtocolError),
        (418, LichessProtocolError),
        (401, LichessConfigurationError),
        (403, LichessConfigurationError),
        (404, LichessUserNotFoundError),
        (408, LichessUnavailableError),
        (500, LichessUnavailableError),
        (503, LichessUnavailableError),
        (600, LichessProtocolError),
    ],
)
async def test_upstream_status_maps_to_plain_service_error(
    status_code: int,
    error_type: type[Exception],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(
                status_code,
                headers={"Content-Type": "text/html"},
                content=b"<html>sensitive upstream body</html>",
            )
        )

        with pytest.raises(error_type):
            await fetch_games_from_lichess(USERNAME)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upstream_429_has_no_retry_after_in_chat_one() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(
            return_value=httpx.Response(
                429,
                headers={"Retry-After": "123"},
                content=b"rate limited",
            )
        )

        with pytest.raises(LichessRateLimitedError) as exc_info:
            await fetch_games_from_lichess(USERNAME)

    assert exc_info.value.retry_after is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_total_timeout_covers_response_body_read(monkeypatch) -> None:
    monkeypatch.setattr(settings, "LICHESS_TOTAL_TIMEOUT_SECONDS", 0.001)
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/plain"},
        stream=CountingStream([PGN_TEXT.encode()], delay=0.05),
    )

    with respx.mock(assert_all_called=True) as router:
        router.get(URL).mock(return_value=response)
        with pytest.raises(LichessUnavailableError):
            await fetch_games_from_lichess(USERNAME)
