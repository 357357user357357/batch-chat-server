"""Dispatch chat_completion() to the right provider based on a model prefix.

  "openai/gpt-4o-mini"                          -> OpenRouter (default, no prefix)
  "vertex:gemini-2.5-flash"                     -> Google Vertex AI
  "bedrock:anthropic.claude-3-5-sonnet-..."     -> AWS Bedrock
  "custom:z-ai/glm-5.3-flash"                   -> custom OpenAI-compatible
                                                   endpoint (CUSTOM_BASE_URL)

The ":flex" processing-tier suffix is stripped BEFORE dispatch: OpenRouter
handles it itself (service_tier="flex" + standard-tier fallback); custom
gateways receive the tier via custom_provider (service_tier + fallback);
Vertex/Bedrock have no flex tier, so they simply run the plain model.
"""

from app.config import settings
from app.services import bedrock, custom_provider, openrouter, tavily, vertex_ai
from app.services.provider_errors import ProviderError

__all__ = ["ProviderError", "chat_completion", "chat_completion_full", "default_models", "configured_status"]


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> str:
    # Strip the processing-tier suffix up front: the OpenRouter branch passes
    # the original model (it does its own tier handling); the others must
    # never see a ":flex" id — it is not part of their model names.
    base_model, tier = openrouter.split_model_variant(model)
    if base_model.startswith("vertex:"):
        return vertex_ai.chat_completion(base_model[len("vertex:"):], messages, temperature, max_tokens)
    if base_model.startswith("bedrock:"):
        return bedrock.chat_completion(base_model[len("bedrock:"):], messages, temperature, max_tokens)
    if base_model.startswith("custom:"):
        return custom_provider.chat_completion(
            base_model[len("custom:"):], messages, temperature, max_tokens, reasoning_effort,
            flex=(tier == "flex"),
        )
    return openrouter.chat_completion(model, messages, temperature, max_tokens,
                                      reasoning_effort=reasoning_effort)


def chat_completion_full(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    """Like chat_completion, but returns a dict with the reply text plus
    OpenRouter metadata (provider, generation id, token counts, cost). Other
    providers return the content only."""
    base_model, tier = openrouter.split_model_variant(model)
    if base_model.startswith("vertex:"):
        return {"content": vertex_ai.chat_completion(
            base_model[len("vertex:"):], messages, temperature, max_tokens)}
    if base_model.startswith("bedrock:"):
        return {"content": bedrock.chat_completion(
            base_model[len("bedrock:"):], messages, temperature, max_tokens)}
    if base_model.startswith("custom:"):
        return custom_provider.chat_completion_full(
            base_model[len("custom:"):], messages, temperature, max_tokens,
            reasoning_effort, flex=(tier == "flex"),
        )
    return openrouter.chat_completion_full(
        model, messages, temperature=temperature, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )


def default_models() -> list[str]:
    models = list(openrouter.DEFAULT_MODELS)
    if custom_provider.is_configured():
        # Custom gateway first when configured — it is the actively used
        # provider; the default model is the user-set one when given.
        defaults = [m for m in openrouter.DEFAULT_MODELS if m.startswith("custom:")]
        head = settings.custom_default_model.strip()
        if head:
            head = f"custom:{head}"
            if head not in defaults:
                defaults.insert(0, head)
        elif not defaults:
            defaults = ["custom:default"]
        models = defaults + models
    if vertex_ai.is_configured():
        models += vertex_ai.DEFAULT_MODELS
    if bedrock.is_configured():
        models += bedrock.DEFAULT_MODELS
    return models


def configured_status() -> dict[str, bool]:
    return {
        "openrouter_configured": bool(settings.openrouter_api_key),
        "custom_configured": custom_provider.is_configured(),
        "vertex_configured": vertex_ai.is_configured(),
        "bedrock_configured": bedrock.is_configured(),
        "tavily_configured": tavily.is_configured(),
    }
