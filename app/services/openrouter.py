import httpx

from app.config import settings

REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

# A sane default list of models for the "batch" feature.
# Users can send requests to several models at once and compare answers.
DEFAULT_MODELS = [
    "openrouter/auto",
    "deepseek/deepseek-chat",
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5",
    "openai/gpt-4o-mini",
    "openai/gpt-4.1",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
]


class OpenRouterError(Exception):
    pass


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        # OpenRouter recommends sending these so creators can see usage
        "HTTP-Referer": "https://github.com/357357user357357/batch-chat-server",
        "X-Title": "Batch Chat Server",
    }


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Call a single OpenRouter model synchronously. Returns the reply text."""
    if not settings.openrouter_api_key:
        raise OpenRouterError("OpenRouter API key is not configured on the server")

    payload: dict = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise OpenRouterError(f"Request failed: {exc}") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(f"Unexpected response from OpenRouter: {data!r}") from exc