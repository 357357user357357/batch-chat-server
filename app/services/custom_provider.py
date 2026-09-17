"""Calls to any OpenAI-compatible provider (no OpenRouter in between).

One catch-all client for the growing zoo of OpenAI-style gateways and
self-hosted servers: FastRouter, GLM/Z.ai, DeepSeek, Together, Groq,
OpenAI itself, LM Studio, vLLM — anything speaking `POST {base_url}/
chat/completions`.

Credentials live in CUSTOM_API_KEY + CUSTOM_BASE_URL (.env or the web
Settings modal). Models are addressed in the UI as "custom:<model>",
e.g. "custom:z-ai/glm-5.3-flash".

What is intentionally NOT available through this provider:
  - reasoning_effort: OpenRouter's unified `reasoning` parameter has no
    OpenAI-compatible meaning (the JSON schema differs per vendor), so it is
    not forwarded — requests stay plain chat completions. (The `flex` flag
    IS forwarded: service_tier="flex" is plain OpenAI API — retried once on
    the standard tier when the gateway rejects it.)
  - Prompt-cache tagging (Anthropic cache_control blocks) and the `usage`
    include flag are OpenRouter niceties; some compatible servers reject
    unknown fields, so nothing extra is sent. A "cached" break-down in the
    response is still surfaced when the vendor happens to return
    prompt_tokens_details.cached_tokens.
"""

import httpx
import time

from app.config import settings
from app.services.openrouter import is_flex_unsupported_error
from app.services.provider_errors import ProviderError

REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

# Picker catalog cache (same pattern as openrouter.py): refreshed hourly so
# changing the provider settings takes effect without a restart.
_CATALOG_TTL_SECONDS = 3600
_CATALOG_CACHE: dict = {"data": None, "ts": 0.0}


class CustomProviderError(ProviderError):
    pass


def is_configured() -> bool:
    """True once a base URL is set. The key may legitimately be empty
    (local LM Studio / vLLM usually run unauthenticated)."""
    return bool(settings.custom_base_url.strip())


def _require_config() -> None:
    if not is_configured():
        raise CustomProviderError(
            "Custom provider is not configured on the server "
            "(set CUSTOM_BASE_URL — and CUSTOM_API_KEY if the endpoint "
            "requires one)"
        )


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.custom_api_key.strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    flex: bool = False,
) -> str:
    """Call one OpenAI-compatible endpoint synchronously. Returns the reply text."""
    return chat_completion_full(model, messages, temperature, max_tokens, flex=flex)["content"]


def chat_completion_full(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    flex: bool = False,
) -> dict:
    """Like chat_completion, but returns a dict — content plus whatever usage
    metadata the vendor reports (token counts; some also report a cost).

    `reasoning_effort` is accepted for signature compatibility with the other
    providers but deliberately ignored (see module docstring).

    `flex` requests OpenAI's Flex processing tier (service_tier="flex") — the
    same treatment the OpenRouter path gives a ":flex" model suffix. If the
    gateway rejects the tier (some validate unknown fields strictly), the
    request is retried once on the standard tier."""
    _require_config()
    payload: dict = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if flex:
        payload["service_tier"] = "flex"

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(
                f"{settings.custom_base_url.strip().rstrip('/')}/chat/completions",
                headers=_headers(),
                json=payload,
            )
            # Flex tier not available on this gateway → standard tier
            if (
                resp.status_code >= 400
                and flex
                and is_flex_unsupported_error(resp.status_code, _safe_error(resp))
            ):
                payload.pop("service_tier", None)
                resp = client.post(
                    f"{settings.custom_base_url.strip().rstrip('/')}/chat/completions",
                    headers=_headers(),
                    json=payload,
                )
            if resp.status_code >= 400:
                raise CustomProviderError(
                    f"Custom provider error (HTTP {resp.status_code}): "
                    f"{_safe_error(resp)}"
                )
            data = resp.json()
    except httpx.HTTPError as exc:
        raise CustomProviderError(f"Request failed: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CustomProviderError(
            f"Unexpected response from the custom provider: {data!r}"
        ) from exc

    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    return {
        "content": content,
        "provider": data.get("provider"),
        "gen_id": data.get("id"),
        "tokens_prompt": usage.get("prompt_tokens"),
        "tokens_cached": prompt_details.get("cached_tokens"),
        "tokens_completion": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cost": usage.get("cost"),
    }


def _safe_error(resp: httpx.Response) -> str:
    """Surface the human-readable `error.message` when the vendor follows the
    OpenAI error shape; otherwise return the raw body head."""
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
        if isinstance(error, str):
            return error
    return str(data)[:300]


def fetch_custom_catalog() -> list[dict]:
    """The custom provider's model catalog for the picker's search, in the
    same shape as OpenRouter's (`id, name, created, context_length, prompt,
    completion`). Most OpenAI-compatible gateways (FastRouter, OpenRouter
    itself) speak the OpenRouter schema — prices as strings, sometimes blank —
    and bare ones (LM Studio, Ollama, vLLM) return only `{"id"}` entries; the
    mapping stays tolerant to both. Models come back WITHOUT the "custom:"
    prefix (the caller adds it), like every other provider's raw ids.

    Never raises — on any failure it returns whatever was cached last
    (possibly [])."""
    now = time.time()
    cached = _CATALOG_CACHE["data"]
    if cached is not None and now - _CATALOG_CACHE["ts"] < _CATALOG_TTL_SECONDS:
        return cached
    if not is_configured():
        return cached or []
    try:
        resp = httpx.get(
            f"{settings.custom_base_url.strip().rstrip('/')}/models",
            headers=_headers(),
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        resp.raise_for_status()
        entries = resp.json().get("data", [])
        if not isinstance(entries, list):
            entries = []
    except Exception:
        return cached or []
    catalog: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        pricing = entry.get("pricing")
        if not isinstance(pricing, dict):
            pricing = {}

        def _price(field: str) -> float:
            # Blank / missing / non-numeric (common) → 0, like OpenRouter's
            # free-tier entries.
            try:
                return max(0.0, float(pricing.get(field) or 0))
            except (TypeError, ValueError):
                return 0.0

        catalog.append(
            {
                "id": entry["id"],
                "name": entry.get("name") or entry["id"],
                "created": entry.get("created"),
                "context_length": entry.get("context_length"),
                "prompt": _price("prompt"),
                "completion": _price("completion"),
            }
        )
    _CATALOG_CACHE["data"] = catalog
    _CATALOG_CACHE["ts"] = now
    return catalog
