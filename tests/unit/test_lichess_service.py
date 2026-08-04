import asyncio

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.config import settings
from app.models.enums import StandardPerfType
from app.services.lichess import (
    LichessBusyError,
    LichessConfigurationError,
    LichessCoordinationError,
    LichessProtocolError,
    LichessRateLimitedError,
    LichessUnavailableError,
    LichessUserNotFoundError,
    fetch_games_from_lichess,
)
from app.services.lichess_gate import (
    LICHESS_COOLDOWN_KEY,
    LICHESS_REQUEST_LOCK_KEY,
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


class BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        self.entered.set()
        await self.release.wait()
        yield PGN_TEXT.encode()


class GateRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls_ms: dict[str, int] = {}
        self.events: list[tuple[object, ...]] = []

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


@pytest.fixture(autouse=True)
def isolated_lichess_gate(monkeypatch) -> GateRedis:
    import app.services.lichess_gate as gate_module

    redis = GateRedis()
    monkeypatch.setattr(gate_module, "_get_redis_client", lambda: redis)
    return redis


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
async def test_upstream_429_sets_bounded_cooldown_before_lock_release(
    isolated_lichess_gate: GateRedis,
) -> None:
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

    assert exc_info.value.retry_after == 123
    assert isolated_lichess_gate.ttls_ms[LICHESS_COOLDOWN_KEY] == 123_000
    cooldown_index = next(
        index
        for index, event in enumerate(isolated_lichess_gate.events)
        if event[0] == "set" and event[1] == LICHESS_COOLDOWN_KEY
    )
    release_index = next(
        index
        for index, event in enumerate(isolated_lichess_gate.events)
        if event[0] == "eval"
    )
    assert cooldown_index < release_index


@pytest.mark.unit
@pytest.mark.asyncio
async def test_request_during_cooldown_makes_no_upstream_call(
    isolated_lichess_gate: GateRedis,
) -> None:
    isolated_lichess_gate.values[LICHESS_COOLDOWN_KEY] = "1"
    isolated_lichess_gate.ttls_ms[LICHESS_COOLDOWN_KEY] = 42_001

    with respx.mock(assert_all_called=False) as router:
        route = router.get(URL).mock(
            return_value=httpx.Response(200, text=PGN_TEXT)
        )

        with pytest.raises(LichessRateLimitedError) as exc_info:
            await fetch_games_from_lichess(USERNAME)

    assert exc_info.value.retry_after == 43
    assert route.call_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parallel_fetch_is_busy_and_makes_only_one_upstream_call() -> None:
    stream = BlockingStream()
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/plain"},
        stream=stream,
    )

    with respx.mock(assert_all_called=True) as router:
        route = router.get(URL).mock(return_value=response)
        first = asyncio.create_task(fetch_games_from_lichess(USERNAME))
        await stream.entered.wait()

        with pytest.raises(LichessBusyError):
            await fetch_games_from_lichess(USERNAME)

        stream.release.set()
        assert await first == PGN_TEXT

    assert route.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_unavailable_makes_no_upstream_call(monkeypatch) -> None:
    import app.services.lichess_gate as gate_module

    class UnavailableRedis(GateRedis):
        async def pttl(self, name: str) -> int:
            raise ConnectionError(f"Redis unavailable for {name}")

    monkeypatch.setattr(
        gate_module,
        "_get_redis_client",
        lambda: UnavailableRedis(),
    )

    with respx.mock(assert_all_called=False) as router:
        route = router.get(URL).mock(
            return_value=httpx.Response(200, text=PGN_TEXT)
        )

        with pytest.raises(LichessCoordinationError):
            await fetch_games_from_lichess(USERNAME)

    assert route.call_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_total_timeout_covers_response_body_read(
    monkeypatch,
    isolated_lichess_gate: GateRedis,
) -> None:
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

    assert LICHESS_REQUEST_LOCK_KEY not in isolated_lichess_gate.values
