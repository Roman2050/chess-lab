from typing import Protocol


class LLMError(Exception):
    """Raised on any generation failure (network, non-2xx, malformed/empty response)."""


class LLMProvider(Protocol):
    """A language-model backend.

    The method is synchronous on purpose: providers are called from the
    sync Celery task that generates reports, where async adds no value.
    """

    def generate(self, system: str, user: str) -> str: ...
