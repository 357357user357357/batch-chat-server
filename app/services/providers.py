"""Dispatch chat_completion() to the right provider based on a model prefix.

  "openai/gpt-4o-mini"                          -> OpenRouter (default, no prefix)
  "vertex:gemini-2.5-flash"                     -> Google Vertex AI
  "bedrock:anthropic.claude-3-5-sonnet-..."     -> AWS Bedrock
"""

from app.config import settings
from app.services import bedrock, openrouter, tavily, vertex_ai
from app.services.provider_errors import ProviderError

__all__ = ["ProviderError", "chat_completion", "default_models", "configured_status"]


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> str:
    if model.startswith("vertex:"):
        return vertex_ai.chat_completion(model[len("vertex:"):], messages, temperature, max_tokens)
    if model.startswith("bedrock:"):
        return bedrock.chat_completion(model[len("bedrock:"):], messages, temperature, max_tokens)
    return openrouter.chat_completion(model, messages, temperature, max_tokens,
                                      reasoning_effort=reasoning_effort)


def default_models() -> list[str]:
    models = list(openrouter.DEFAULT_MODELS)
    if vertex_ai.is_configured():
        models += vertex_ai.DEFAULT_MODELS
    if bedrock.is_configured():
        models += bedrock.DEFAULT_MODELS
    return models


def configured_status() -> dict[str, bool]:
    return {
        "openrouter_configured": bool(settings.openrouter_api_key),
        "vertex_configured": vertex_ai.is_configured(),
        "bedrock_configured": bedrock.is_configured(),
        "tavily_configured": tavily.is_configured(),
    }
