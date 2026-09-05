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
#
# Model ids may carry a processing-tier suffix:
#   "…:flex"  → OpenAI Flex processing (service_tier="flex"): cheaper, slower
#               synchronous runs. If the provider rejects the tier for that
#               model (some — like Astra — only serve it sometimes), the server
#               automatically falls back to a standard-tier request.
#   "…:batch" → async Batch API (≈50% off, 24h window) — see create_batch().
# Keeping tiers as plain suffixes means any future model works with zero code
# changes: just type "vendor/new-model:flex" in the picker.
DEFAULT_MODELS = [
    "openai/gpt-6-astra",
    "openai/gpt-6-astra-pro",
    "~deepseek/deepseek-v4-flash-latest",
    "anthropic/claude-fable-5.1",
]

# Defaults per mode: DeepSeek v4 flash (latest) answers live chats; Fable 5.1
# runs the batch chats (the ⚡ JSONL batch modal defaults to its :batch id).
DEFAULT_LIVE_MODEL = "~deepseek/deepseek-v4-flash-latest"

# Default batch model: discounted async Batch API (≈50% of model price)
DEFAULT_BATCH_MODEL = "anthropic/claude-fable-5.1:batch"

# Known processing-tier suffixes (see DEFAULT_MODELS above).
FLEX_SUFFIX = ":flex"
BATCH_SUFFIX = ":batch"


def split_model_variant(model: str) -> tuple[str, str | None]:
    """Split "vendor/model[:tier]" into (base_model, tier) where tier is
    "flex" | "batch" | None. Batch keeps its suffix (it is part of the
    OpenRouter model id); flex is a request-level tier and is stripped."""
    stripped = model.strip()
    if stripped.endswith(FLEX_SUFFIX):
        return stripped[: -len(FLEX_SUFFIX)], "flex"
    if stripped.endswith(BATCH_SUFFIX):
        return stripped, "batch"
    return stripped, None


def is_reasoning_unsupported_error(status_code: int, message: str) -> bool:
    """OpenRouter rejected the reasoning param for this model (e.g. astra:
    "Reasoning is mandatory for this endpoint and cannot be disabled")."""
    if status_code != 400:
        return False
    t = (message or "").lower()
    return "reasoning" in t and (
        "cannot be disabled" in t
        or "not supported" in t
        or "mandatory" in t
        or "does not support" in t
    )


# Backwards-compatible alias used by the chat send path.
_is_reasoning_unsupported_error = is_reasoning_unsupported_error


def is_flex_unsupported_error(status_code: int, message: str) -> bool:
    """True when the provider rejected the flex processing tier itself (the
    model exists but not via flex) — callers then fall back to a standard
    request (or the Batch API for bulk work)."""
    if status_code != 400:
        return False
    lowered = message.lower()
    return "service_tier" in lowered or "flex" in lowered


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
    reasoning_effort: str | None = None,
) -> str:
    """Call a single OpenRouter model synchronously. Returns the reply text.

    A ":flex" model suffix requests the Flex processing tier
    (service_tier="flex"): cheaper in exchange for slower processing. When the
    provider does not offer flex for the model (e.g. some Astra releases), the
    request is automatically retried on the standard tier so the chat still
    works.

    `reasoning_effort` controls the model's thinking budget via OpenRouter's
    unified `reasoning` parameter: "none" disables reasoning entirely, any of
    low/medium/high/xhigh/max sets the effort level. None (default) leaves the
    model's own default untouched.
    """
    _require_key()
    base_model, tier = split_model_variant(model)
    messages = _with_prompt_cache(messages, settings.cache_duration_seconds)

    payload: dict = {"model": base_model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tier == "flex":
        payload["service_tier"] = "flex"
    if reasoning_effort == "none":
        payload["reasoning"] = {"enabled": False}
    elif reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                error_text = _safe_error(resp)
                # Flex tier not available for this model → standard tier
                if (
                    tier == "flex"
                    and is_flex_unsupported_error(resp.status_code, error_text)
                ):
                    payload.pop("service_tier", None)
                    resp = client.post(
                        f"{settings.openrouter_base_url}/chat/completions",
                        headers=_headers(),
                        json=payload,
                    )
                    error_text = _safe_error(resp) if resp.status_code >= 400 else ""
                # Reasoning param rejected (e.g. "Reasoning is mandatory for
                # this endpoint and cannot be disabled" on reasoning-only
                # models) → retry once without it (model default applies).
                if (
                    resp.status_code >= 400
                    and "reasoning" in payload
                    and _is_reasoning_unsupported_error(resp.status_code, error_text)
                ):
                    payload.pop("reasoning", None)
                    resp = client.post(
                        f"{settings.openrouter_base_url}/chat/completions",
                        headers=_headers(),
                        json=payload,
                    )
                if resp.status_code >= 400:
                    raise OpenRouterError(
                        f"OpenRouter error (HTTP {resp.status_code}): "
                        f"{_safe_error(resp)}"
                    )
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

    A ":flex" model suffix tags every request with service_tier="flex"
    (Flex processing through the Batch API — the cheapest path, used as the
    fallback when the flex tier is not available for synchronous Astra calls).
    Returns the raw OpenRouter batch object (status is usually "validating").
    """
    _require_key()
    base_model, tier = split_model_variant(model)

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
            if tier == "flex":
                new_body["service_tier"] = "flex"
            item["body"] = new_body
        cached_requests.append(item)

    payload = {
        # The docs require endpoint and model serialized BEFORE requests
        "endpoint": "/v1/chat/completions",
        "model": base_model,
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
    """Surface OpenRouter's human-readable `error.message` when present."""
    try:
        data = resp.json()
    except Exception:
        return resp.text[:300]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if message:
                return str(message)
    return str(data)[:300]