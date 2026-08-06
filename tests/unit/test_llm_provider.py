import json

import httpx
import pytest
import respx

from app.services.llm.base import LLMError
from app.services.llm.openai_compat import OpenAICompatibleProvider

BASE_URL = "http://localhost:11434/v1"
COMPLETIONS_URL = f"{BASE_URL}/chat/completions"


def _make_provider(
    api_key: str | None = None,
    model: str = "llama3.1",
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=BASE_URL,
        model=model,
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

        payload = json.loads(router.calls[0].request.read())

        assert payload["model"] == "llama3.1"
        assert payload["temperature"] == 0.4
        assert payload["stream"] is False
        assert payload["messages"] == [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USR"},
        ]


@pytest.mark.unit
def test_generate_omits_temperature_for_gpt5_model() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=_completion_response("ok"))

        _make_provider(model="gpt-5.6-terra").generate(system="s", user="u")

        payload = json.loads(router.calls[0].request.read())
        assert payload["model"] == "gpt-5.6-terra"
        assert "temperature" not in payload


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
def test_generate_includes_structured_error_message_on_http_400() -> None:
    response = httpx.Response(
        400,
        json={
            "error": {
                "message": "Unsupported parameter:\n  'temperature'",
                "type": "invalid_request_error",
            }
        },
    )
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=response)

        with pytest.raises(LLMError) as exc_info:
            _make_provider().generate(system="s", user="u")

    assert str(exc_info.value) == (
        "LLM request failed with status 400: "
        "Unsupported parameter: 'temperature'"
    )


@pytest.mark.unit
def test_generate_omits_unstructured_error_body_from_llmerror() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=httpx.Response(500, text="boom"))

        with pytest.raises(LLMError) as exc_info:
            _make_provider().generate(system="s", user="u")

    assert str(exc_info.value) == "LLM request failed with status 500"


@pytest.mark.unit
def test_generate_omits_auth_error_detail_from_llmerror() -> None:
    response = httpx.Response(
        401,
        json={"error": {"message": "Incorrect API key: sk-secret-value"}},
    )
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=response)

        with pytest.raises(LLMError) as exc_info:
            _make_provider().generate(system="s", user="u")

    assert str(exc_info.value) == "LLM request failed with status 401"


@pytest.mark.unit
def test_generate_truncates_structured_error_message() -> None:
    response = httpx.Response(400, json={"error": {"message": "x" * 501}})
    with respx.mock(assert_all_called=True) as router:
        router.post(COMPLETIONS_URL).mock(return_value=response)

        with pytest.raises(LLMError) as exc_info:
            _make_provider().generate(system="s", user="u")

    assert str(exc_info.value) == (
        f"LLM request failed with status 400: {'x' * 500}..."
    )


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
