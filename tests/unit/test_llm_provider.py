import httpx
import pytest
import respx

from app.services.llm.base import LLMError
from app.services.llm.openai_compat import OpenAICompatibleProvider

BASE_URL = "http://localhost:11434/v1"
COMPLETIONS_URL = f"{BASE_URL}/chat/completions"


def _make_provider(api_key: str | None = None) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=BASE_URL,
        model="llama3.1",
        api_key=api_key,
        temperature=0.4,
        timeout=120,
    )


def _completion_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


@pytest.mark.unit
def test_generate_builds_correct_request() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.post(COMPLETIONS_URL).mock(
            return_value=_completion_response("ok")
        )

        result = _make_provider().generate(system="SYS", user="USR")

        assert result == "ok"
        assert route.call_count == 1

        body = router.calls[0].request.read()
        import json

        payload = json.loads(body)

        assert payload["model"] == "llama3.1"
        assert payload["temperature"] == 0.4
        assert payload["stream"] is False
        assert payload["messages"] == [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USR"},
        ]


@pytest.mark.unit
def test_generate_parses_content() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=_completion_response("hello"))

        assert _make_provider().generate(system="s", user="u") == "hello"


@pytest.mark.unit
def test_generate_sends_auth_header_when_key_set() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=_completion_response("ok"))

        _make_provider(api_key="secret-key").generate(system="s", user="u")

        assert router.calls[0].request.headers["Authorization"] == "Bearer secret-key"


@pytest.mark.unit
def test_generate_omits_auth_header_when_key_none() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=_completion_response("ok"))

        _make_provider(api_key=None).generate(system="s", user="u")

        assert "Authorization" not in router.calls[0].request.headers


@pytest.mark.unit
def test_generate_raises_llmerror_on_http_500() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=httpx.Response(500, text="boom"))

        with pytest.raises(LLMError):
            _make_provider().generate(system="s", user="u")


@pytest.mark.unit
def test_generate_raises_llmerror_on_malformed_json() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(LLMError):
            _make_provider().generate(system="s", user="u")


@pytest.mark.unit
def test_generate_raises_llmerror_on_empty_content() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=_completion_response(""))

        with pytest.raises(LLMError):
            _make_provider().generate(system="s", user="u")
