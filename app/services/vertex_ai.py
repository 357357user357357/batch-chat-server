"""Direct calls to Google Vertex AI (no OpenRouter in between).

Auth uses a GCP service-account key (JSON), either pasted whole into
GOOGLE_SERVICE_ACCOUNT_JSON or mounted as a file and pointed to by
GOOGLE_SERVICE_ACCOUNT_FILE. Models are addressed in the UI as
"vertex:<model>", e.g. "vertex:gemini-2.5-flash" or
"vertex:claude-sonnet-4-5@20250929" (Anthropic models via Vertex Model Garden).
"""

import json
import threading

import httpx

from app.config import settings
from app.services.provider_errors import ProviderError

DEFAULT_MODELS = [
    "vertex:gemini-2.5-flash",
    "vertex:gemini-2.5-pro",
]

REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=15.0)
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_token_lock = threading.Lock()
_cached_token: str | None = None
_cached_credentials = None


class VertexAIError(ProviderError):
    pass


def is_configured() -> bool:
    return bool(settings.google_project_id) and bool(
        settings.google_service_account_json or settings.google_service_account_file
    )


def _require_config() -> None:
    if not settings.google_project_id:
        raise VertexAIError("GOOGLE_PROJECT_ID is not configured on the server")
    if not (settings.google_service_account_json or settings.google_service_account_file):
        raise VertexAIError(
            "Google service-account credentials are not configured on the server "
            "(set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE)"
        )


def _load_credentials():
    global _cached_credentials
    if _cached_credentials is not None:
        return _cached_credentials
    try:
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover
        raise VertexAIError("google-auth is not installed on the server") from exc

    try:
        if settings.google_service_account_json:
            info = json.loads(settings.google_service_account_json)
            _cached_credentials = service_account.Credentials.from_service_account_info(
                info, scopes=_SCOPES
            )
        else:
            _cached_credentials = service_account.Credentials.from_service_account_file(
                settings.google_service_account_file, scopes=_SCOPES
            )
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        raise VertexAIError(f"Invalid Google service-account credentials: {exc}") from exc
    return _cached_credentials


def _access_token() -> str:
    global _cached_token
    _require_config()
    from google.auth.transport.requests import Request

    with _token_lock:
        creds = _load_credentials()
        if not creds.valid:
            creds.refresh(Request())
        _cached_token = creds.token
    return _cached_token


def _split_messages(messages: list[dict[str, str]]) -> tuple[str | None, list[dict]]:
    """Split OpenAI-style messages into (system_text, gemini "contents" list)."""
    system_parts: list[str] = []
    contents: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            system_parts.append(content)
            continue
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": content}],
        })
    return ("\n".join(system_parts) if system_parts else None), contents


def _is_anthropic_model(model: str) -> bool:
    return "claude" in model.lower()


def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Call a Vertex AI model synchronously. Returns the reply text."""
    _require_config()
    token = _access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = (
        f"https://{settings.google_location}-aiplatform.googleapis.com/v1"
        f"/projects/{settings.google_project_id}/locations/{settings.google_location}"
        "/publishers"
    )

    if _is_anthropic_model(model):
        url = f"{base}/anthropic/models/{model}:rawPredict"
        system_text, _ = _split_messages(messages)
        anthropic_messages = [
            {"role": "assistant" if m.get("role") == "assistant" else "user", "content": m.get("content", "")}
            for m in messages
            if m.get("role") != "system"
        ]
        payload: dict = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
        }
        if system_text:
            payload["system"] = system_text
        if temperature is not None:
            payload["temperature"] = temperature
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code >= 400:
                    raise VertexAIError(f"Vertex AI (HTTP {resp.status_code}): {_safe_error(resp)}")
                data = resp.json()
        except httpx.HTTPError as exc:
            raise VertexAIError(f"Vertex AI request failed: {exc}") from exc
        try:
            return "".join(b.get("text", "") for b in data.get("content", []))
        except (KeyError, TypeError) as exc:
            raise VertexAIError(f"Unexpected response from Vertex AI: {data!r}") from exc

    url = f"{base}/google/models/{model}:generateContent"
    system_text, contents = _split_messages(messages)
    payload = {"contents": contents}
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}
    generation_config: dict = {}
    if temperature is not None:
        generation_config["temperature"] = temperature
    if max_tokens is not None:
        generation_config["maxOutputTokens"] = max_tokens
    if generation_config:
        payload["generationConfig"] = generation_config

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise VertexAIError(f"Vertex AI (HTTP {resp.status_code}): {_safe_error(resp)}")
            data = resp.json()
    except httpx.HTTPError as exc:
        raise VertexAIError(f"Vertex AI request failed: {exc}") from exc

    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError) as exc:
        raise VertexAIError(f"Unexpected response from Vertex AI: {data!r}") from exc


def _safe_error(resp: httpx.Response) -> str:
    try:
        return str(resp.json())
    except Exception:
        return resp.text[:300]
