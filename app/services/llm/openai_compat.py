import httpx

from app.services.llm.base import LLMError


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
            "temperature": self.temperature,
            "stream": False,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"LLM request failed with status {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("LLM returned a malformed response") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM returned empty content")

        return content
