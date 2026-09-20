import json
import re
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
    "openai/gpt-5.6-sol",
    "openai/gpt-5.6-sol-pro",
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

# OpenRouter catalog cache (public /models endpoint, no key needed).
_CATALOG_CACHE: dict = {"data": None, "ts": 0.0}
_PRICING_TTL_SECONDS = 3600.0


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


def fetch_model_catalog() -> list[dict]:
    """The full public OpenRouter model catalog (id, name, release date,
    context length, per-token pricing), cached for an hour. Never raises —
    on any failure it returns whatever was cached last (possibly [])."""
    now = time.time()
    cached = _CATALOG_CACHE["data"]
    if cached is not None and now - _CATALOG_CACHE["ts"] < _PRICING_TTL_SECONDS:
        return cached
    try:
        resp = httpx.get(
            "https://openrouter.ai/api/v1/models",
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        resp.raise_for_status()
        entries = resp.json().get("data", [])
    except Exception:
        return cached or []
    catalog: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        raw = entry.get("pricing") or {}
        try:
            # Negative values are catalog placeholders (e.g. openrouter/auto
            # at -1) — clamp to 0 so sorting/display never go insane.
            prompt = max(0.0, float(raw.get("prompt") or 0))
            completion = max(0.0, float(raw.get("completion") or 0))
        except (TypeError, ValueError):
            continue
        catalog.append(
            {
                "id": entry["id"],
                "name": entry.get("name") or entry["id"],
                "created": entry.get("created"),
                "context_length": entry.get("context_length"),
                "prompt": prompt,
                "completion": completion,
            }
        )
    _CATALOG_CACHE["data"] = catalog
    _CATALOG_CACHE["ts"] = now
    return catalog


def fetch_model_pricing() -> dict[str, dict[str, float]]:
    """Per-token model pricing (USD) from the public OpenRouter catalog,
    keyed by plain base model id (derived from the shared catalog cache)."""
    return {m["id"]: {"prompt": m["prompt"], "completion": m["completion"]}
            for m in fetch_model_catalog()}


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
    request (or the Batch API for bulk work). OpenRouter answers 400; strict
    OpenAI-compatible gateways (pydantic-style validation) answer 422 — both
    only count when the message names the tier."""
    if status_code not in (400, 422):
        return False
    lowered = message.lower()
    return "service_tier" in lowered or "flex" in lowered


class OpenRouterError(ProviderError):
    pass


def _max_token_limit_from_error(text: str) -> int | None:
    """Provider-stated max output tokens cap from a 400/422 error body, or None.

    When a request omits `max_tokens`, OpenRouter substitutes the model's
    catalog maximum, which some providers reject outright, e.g. Google:
    "Requested maximum tokens of 131072 exceeds the maximum output tokens
    limit: 102400." Mirrors the phone app's token-limits.ts helper.
    """
    match = re.search(r"max(?:imum)? output tokens limit:\s*(\d+)", text or "")
    return int(match.group(1)) if match else None


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
    """Call a single OpenRouter model synchronously. Returns the reply text."""
    return chat_completion_full(
        model, messages, temperature=temperature, max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )["content"]


def parse_sse_chat_stream(lines) -> dict:
    """Fold an OpenAI-compatible SSE line iterable into the same dict shape
    the buffered chat/completions body produces (content + usage metadata).

    Chat answers are fetched with `"stream": true` on purpose: while a queued
    ":flex" request waits, OpenRouter ships keep-alive comment lines
    (": OPENROUTER PROCESSING"), so neither httpx's per-read timeout nor any
    NAT'd connection in between ever sees a silent gap — buffered requests
    died on exactly that.
    """
    content_parts: list[str] = []
    provider: str | None = None
    gen_id: str | None = None
    usage: dict = {}
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        line = raw.strip()
        if not line or line.startswith(":"):
            continue  # keep-alive comment (": OPENROUTER PROCESSING")
        if not line.startswith("data:"):
            continue  # OpenRouter uses no other SSE fields here
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue  # tolerate a malformed line instead of losing the answer
        if not isinstance(chunk, dict):
            continue
        if isinstance(chunk.get("id"), str) and chunk["id"]:
            gen_id = chunk["id"]
        if isinstance(chunk.get("provider"), str) and chunk["provider"]:
            provider = chunk["provider"]
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]  # final chunk, thanks to usage.include
        choices = chunk.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            delta = choices[0].get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                content_parts.append(delta["content"])
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    return {
        "content": "".join(content_parts),
        "provider": provider,
        "gen_id": gen_id,
        "tokens_prompt": usage.get("prompt_tokens"),
        "tokens_cached": prompt_details.get("cached_tokens"),
        "tokens_completion": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cost": usage.get("cost"),
    }


def _chat_payload(
    base_model: str,
    tier: str | None,
    messages: list[dict],
    temperature: float | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
) -> dict:
    payload: dict = {"model": base_model, "messages": messages, "stream": True}
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
    # Ask OpenRouter to report exact usage (token counts + cost) — with
    # streaming it arrives in the final chunk's `usage` field. Without this,
    # streamed generations can end up as 0-tok/$0.00 rows in the logs page.
    payload["usage"] = {"include": True}
    return payload


def _stream_chat_once(payload: dict) -> tuple[int, str, dict | None]:
    """One streamed chat attempt → (status_code, error_text, parsed_data).

    status_code 0 means a transport-level failure (error_text has the
    detail). REQUEST_TIMEOUT's read component acts as an IDLE timeout here:
    OpenRouter's keep-alives during flex queueing keep resetting it, while a
    genuinely dead connection still fails after 180 s of silence.
    """
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            with client.stream(
                "POST",
                f"{settings.openrouter_base_url}/chat/completions",
                headers=_headers(),
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    try:
                        resp.read()  # load the body so _safe_error can parse it
                    except httpx.HTTPError:
                        pass
                    return resp.status_code, _safe_error(resp), None
                return 200, "", parse_sse_chat_stream(resp.iter_lines())
    except httpx.HTTPError as exc:
        return 0, f"Request failed: {exc}", None


def chat_completion_full(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    """Like chat_completion, but also returns the per-message metadata the web
    UI shows "as in OpenRouter": provider, generation id, token counts, cost.

    `reasoning_effort` controls the model's thinking budget via OpenRouter's
    unified `reasoning` parameter: "none" disables reasoning entirely, any of
    low/medium/high/xhigh/max sets the effort level. None (default) leaves the
    model's own default untouched.

    The request is STREAMED (SSE): OpenRouter sends keep-alive comments while
    a ":flex" request sits in the provider queue, which keeps the connection
    alive through long waits that killed the old buffered request.
    """
    _require_key()
    base_model, tier = split_model_variant(model)
    messages = _with_prompt_cache(messages, settings.cache_duration_seconds)

    payload = _chat_payload(
        base_model, tier, messages, temperature, max_tokens, reasoning_effort
    )

    status, error_text, data = _stream_chat_once(payload)
    if status == 0:
        raise OpenRouterError(error_text)
    if status >= 400:
        # Flex tier not available for this model → standard tier
        if tier == "flex" and is_flex_unsupported_error(status, error_text):
            payload.pop("service_tier", None)
            status, error_text, data = _stream_chat_once(payload)
        # Reasoning param rejected (e.g. "Reasoning is mandatory for this
        # endpoint and cannot be disabled" on reasoning-only models) → retry
        # once without it (model default applies).
        if (
            status >= 400
            and "reasoning" in payload
            and is_reasoning_unsupported_error(status, error_text)
        ):
            payload.pop("reasoning", None)
            status, error_text, data = _stream_chat_once(payload)
        # Provider caps max output tokens below what was requested
        # (OpenRouter substitutes the model's catalog maximum when the
        # request omits max_tokens; e.g. Google: "Requested maximum tokens
        # of 131072 exceeds the maximum output tokens limit: 102400") →
        # retry once clamped to that limit.
        if status >= 400:
            token_limit = _max_token_limit_from_error(error_text)
            if token_limit:
                payload["max_tokens"] = token_limit
                status, error_text, data = _stream_chat_once(payload)
        if status >= 400:
            raise OpenRouterError(f"OpenRouter error (HTTP {status}): {error_text}")

    content = data["content"]
    if not isinstance(content, str) or not content.strip():
        # Rare provider hiccup: HTTP 200 with empty content (seen on flaky
        # Astra/Google endpoints) → one automatic retry before surfacing an
        # empty answer to the user.
        retry_status, _retry_error, retry_data = _stream_chat_once(payload)
        if (
            retry_status == 200
            and isinstance(retry_data["content"], str)
            and retry_data["content"].strip()
        ):
            data = retry_data
    return data


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