import time

import httpx

from app.config import settings
from app.services.provider_errors import ProviderError

REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

# Terminal statuses of the OpenRouter async Batch API
BATCH_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "expired", "cancelled"}
)
BATCH_ERROR_STATUSES = frozenset({"failed", "expired", "cancelled"})

# A sane default list of models for the "batch" feature.
# Users can send requests to several models at once and compare answers.
DEFAULT_MODELS = [
    "deepseek/deepseek-v4-flash-0731",
    "deepseek/deepseek-chat",
    "openai/gpt-4o-mini",
    "openai/gpt-4.1",
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.5",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "openrouter/auto",
]

# Default batch model: discounted async Batch API (≈50% of model price)
DEFAULT_BATCH_MODEL = "anthropic/claude-fable-5:batch"


class OpenRouterError(ProviderError):
    pass


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        # OpenRouter recommends sending these so creators can see usage
        "HTTP-Referer": "https://github.com/357357user357357/batch-chat-server",
        "X-Title": "Batch Chat Server",
    }


def _require_key() -> None:
    if not settings.openrouter_api_key:
        raise OpenRouterError("OpenRouter API key is not configured on the server")


def _with_prompt_cache(
    messages: list[dict[str, str]],
    ttl_seconds: int,
) -> list[dict]:
    """Tag the stable message prefix with an Anthropic `cache_control` block.

    Mirrors the Android app: the breakpoint is the second-to-last message (the
    final message is the new question/turn and stays dynamic). TTL >= 1 hour
    sends the extended `"1h"` cache; anything else uses the ~5 minute
    `ephemeral` default (a numeric ttl is silently dropped by OpenRouter, so a
    30-minute value must not be emitted as a number).
    """
    if ttl_seconds <= 0 or not messages:
        return messages
    cache_control: dict = {"type": "ephemeral"}
    if ttl_seconds >= 3600:
        cache_control["ttl"] = "1h"
    breakpoint_index = len(messages) - 2 if len(messages) >= 2 else 0
    out: list[dict] = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if index == breakpoint_index and isinstance(content, str):
            out.append(
                {
                    "role": message.get("role", "user"),
                    "content": [
                        {"type": "text", "text": content, "cache_control": cache_control},
                    ],
                }
            )
        else:
            out.append(message)
    return out


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Call a single OpenRouter model synchronously. Returns the reply text."""
    _require_key()
    messages = _with_prompt_cache(messages, settings.cache_duration_seconds)

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


# ---------------------------------------------------------------------------
# Async Batch API (https://openrouter.ai/docs/batch-quickstart)
# ---------------------------------------------------------------------------


def create_batch(model: str, requests: list[dict]) -> dict:
    """Submit an async batch. `requests` = [{custom_id, body}, ...].

    Returns the raw OpenRouter batch object (status is usually "validating").
    """
    _require_key()

    cached_requests: list[dict] = []
    for request in requests:
        item = dict(request)
        body = item.get("body")
        if isinstance(body, dict):
            new_body = dict(body)
            messages = new_body.get("messages")
            if isinstance(messages, list):
                new_body["messages"] = _with_prompt_cache(
                    messages, settings.cache_duration_seconds
                )
            item["body"] = new_body
        cached_requests.append(item)

    payload = {
        # The docs require endpoint and model serialized BEFORE requests
        "endpoint": "/v1/chat/completions",
        "model": model,
        "requests": cached_requests,
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(
                f"{settings.openrouter_base_url}/beta/batches",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                raise OpenRouterError(
                    f"OpenRouter batch create failed (HTTP {resp.status_code}): "
                    f"{_safe_error(resp)}"
                )
            return resp.json()
    except httpx.HTTPError as exc:
        raise OpenRouterError(f"Batch create request failed: {exc}") from exc


def get_batch(batch_id: str) -> dict:
    """Fetch a batch. Retries transient 404/5xx (the beta API can 404 a fresh
    batch) — same behavior as the Android app."""
    _require_key()
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                resp = client.get(
                    f"{settings.openrouter_base_url}/beta/batches/{batch_id}",
                    headers=_headers(),
                )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404 or resp.status_code >= 500:
                if attempt < max_attempts:
                    time.sleep(1.5 * attempt)
                    continue
                raise OpenRouterError(
                    f"Unable to fetch batch {batch_id} (HTTP {resp.status_code}): "
                    f"{_safe_error(resp)}"
                )
            raise OpenRouterError(
                f"Unable to fetch batch {batch_id} (HTTP {resp.status_code}): "
                f"{_safe_error(resp)}"
            )
        except httpx.HTTPError as exc:
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)
                continue
            raise OpenRouterError(f"Failed to fetch batch {batch_id}: {exc}") from exc
    raise OpenRouterError(f"Unable to fetch batch {batch_id}.")


def is_batch_terminal(status: str) -> bool:
    return status in BATCH_TERMINAL_STATUSES


def is_batch_error(status: str) -> bool:
    return status in BATCH_ERROR_STATUSES


def extract_batch_answer(result: dict) -> tuple[str, str | None, str | None]:
    """(status, answer_text, error_text) for one OpenRouter batch result item."""
    if result.get("error"):
        error = result["error"]
        error = error if isinstance(error, str) else str(error)
        return "failed", None, error

    response = result.get("response") or {}
    if not response or response.get("status_code") != 200:
        code = response.get("status_code", "?") if isinstance(response, dict) else "?"
        return "failed", None, f"HTTP {code}"

    body = response.get("body") or {}
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = ""
    if not content:
        return "failed", None, "Empty response from the model"
    return "completed", content, None


def _safe_error(resp: httpx.Response) -> str:
    try:
        return str(resp.json())
    except Exception:
        return resp.text[:300]