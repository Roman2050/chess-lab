from app.config import settings
from app.services.llm.base import LLMProvider
from app.services.llm.openai_compat import OpenAICompatibleProvider


def get_llm_provider() -> LLMProvider:
    """Build the configured LLM provider from settings.

    Only one implementation today; the factory is the extension point for
    additional (non-OpenAI-compatible) providers later.
    """
    return OpenAICompatibleProvider(
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY,
        temperature=settings.LLM_TEMPERATURE,
        timeout=settings.LLM_TIMEOUT,
    )
