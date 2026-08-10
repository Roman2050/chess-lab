import httpx

from app.services.llm.base import LLMError

_MAX_ERROR_DETAIL_LENGTH = 500
_SENSITIVE_ERROR_STATUSES = frozenset({401, 403})


def _supports_temperature(model: str) -> bool:
    return not model.casefold().startswith("gpt-5")


def _safe_error_detail(response: httpx.Response) -> str | None:
    if response.status_code in _SENSITIVE_ERROR_STATUSES:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None

    error = data.get("error")
    if not isinstance(error, dict):
        return None

    message = error.get("message")
    if not isinstance(message, str):
        return None

    normalized = " ".join(message.split())
    if not normalized:
        return None

    if len(normalized) > _MAX_ERROR_DETAIL_LENGTH:
        return f"{normalized[:_MAX_ERROR_DETAIL_LENGTH]}..."
    return normalized


class OpenAICompatibleProvider:
    """LLM provider for any OpenAI-compatible /chat/completions endpoint.

    Covers Ollama, vLLM, LM Studio, OpenRouter, OpenAI, etc. Switching
    backends is purely a matter of base_url/model/api_key — no code change.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None,
        temperature: float,
        timeout: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout

    def generate(self, system: str, user: str) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if _supports_temperature(self.model):
            payload["temperature"] = self.temperature

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            message = f"LLM request failed with status {exc.response.status_code}"
            detail = _safe_error_detail(exc.response)
            if detail:
                message = f"{message}: {detail}"
            raise LLMError(message) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("LLM returned a malformed response") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM returned empty content")

        return content
