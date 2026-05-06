import pytest
import respx
import httpx

from app.models.enums import StandardPerfType
from app.services.lichess import fetch_games_from_lichess


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_games_from_lichess_sends_expected_query_params_with_perf_type() -> None:
    username = "someuser"
    max_games = 37

    url = f"https://lichess.org/api/games/user/{username}"
    response_text = "PGN TEXT"

    with respx.mock(assert_all_called=True) as router:
        route = router.get(url).mock(return_value=httpx.Response(200, text=response_text))

        result = await fetch_games_from_lichess(
            username=username,
            max_games=max_games,
            perf_type=StandardPerfType.blitz,
        )

        assert result == response_text

        assert route.called
        assert route.call_count == 1

        request = route.calls[0].request
        params = request.url.params

        assert params["perfType"] == "blitz"
        assert int(params["max"]) == max_games
        assert params["tags"] == "true"
        assert params["clocks"] == "false"
        assert params["evals"] == "false"
        assert params["opening"] == "true"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_games_from_lichess_uses_default_perf_type_when_none() -> None:
    username = "someuser"
    max_games = 12

    url = f"https://lichess.org/api/games/user/{username}"
    response_text = "PGN TEXT"

    with respx.mock(assert_all_called=True) as router:
        route = router.get(url).mock(return_value=httpx.Response(200, text=response_text))

        result = await fetch_games_from_lichess(
            username=username,
            max_games=max_games,
            perf_type=None,
        )

        assert result == response_text

        assert route.called
        assert route.call_count == 1

        request = route.calls[0].request
        params = request.url.params

        assert params["perfType"] == "ultraBullet,bullet,blitz,rapid,classical,correspondence"
        assert int(params["max"]) == max_games
        assert params["tags"] == "true"
        assert params["clocks"] == "false"
        assert params["evals"] == "false"
        assert params["opening"] == "true"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_games_from_lichess_returns_response_text_on_success() -> None:
    username = "someuser"
    url = f"https://lichess.org/api/games/user/{username}"

    response_text = "SOME PGN CONTENT\n\n1. e4 e5 1-0\n"

    with respx.mock(assert_all_called=True) as router:
        router.get(url).mock(return_value=httpx.Response(200, text=response_text))

        result = await fetch_games_from_lichess(username=username)

        assert result == response_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_games_from_lichess_raises_http_status_error_on_non_2xx() -> None:
    username = "someuser"
    url = f"https://lichess.org/api/games/user/{username}"

    with respx.mock(assert_all_called=True) as router:
        router.get(url).mock(return_value=httpx.Response(404, text="Not Found"))

        with pytest.raises(httpx.HTTPStatusError):
            await fetch_games_from_lichess(username=username)