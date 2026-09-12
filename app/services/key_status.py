"""Liveness checks + stored status for the provider keys pasted in the web
Settings modal (OpenRouter and Tavily).

Every time the owner saves/pastes one of these keys (or presses "Check"), the
provider's cheapest read-only endpoint is called and the verdict is remembered
in `app_settings` rows named `<field>.status` (a JSON blob). Those rows are
deliberately OUTSIDE ALLOWED_FIELDS in settings_store — they are UI metadata,
never applied onto the `settings` singleton and never exported in credential
backups.
"""

import json
import time

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting, utcnow

# Fields with a cheap, free status endpoint. OpenRouter and Tavily ship
# dedicated account endpoints; the custom provider checks its whole
# configured endpoint (key + base URL together) via GET {base_url}/models.
CHECKED_FIELDS = ["openrouter_api_key", "custom_api_key", "tavily_api_key"]

REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
STATUS_TTL_SECONDS = 10 * 60  # older results are flagged stale in the UI


def _row_key(field: str) -> str:
    return f"{field}.status"


def clear_status(db: Session, field: str) -> None:
    """Forget the stored verdict (key deleted or about to be re-checked)."""
    row = db.get(AppSetting, _row_key(field))
    if row is not None:
        db.delete(row)
        db.commit()


def store_status(db: Session, field: str, result: dict) -> None:
    row = db.get(AppSetting, _row_key(field))
    payload = json.dumps(result, ensure_ascii=False)
    if row is None:
        db.add(AppSetting(key=_row_key(field), value=payload))
    else:
        row.value = payload
    db.commit()


def get_status(db: Session, field: str) -> dict | None:
    """The stored check result (None when never checked), plus a `stale` flag."""
    if field not in CHECKED_FIELDS:
        return None
    row = db.get(AppSetting, _row_key(field))
    if row is None or not row.value:
        return None
    try:
        data = json.loads(row.value)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    checked_at = data.get("checked_at_epoch") or 0
    data["stale"] = (utcnow().timestamp() - checked_at) > STATUS_TTL_SECONDS
    return data


def _http_check(url: str, headers: dict) -> tuple[int, dict | None, str]:
    """(http_status, parsed_json_or_None, network_error_text_or_empty)."""
    try:
        resp = httpx.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        return 0, None, f"Network error: {exc}"
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = None
    return resp.status_code, data, ""


def check_openrouter_key(key: str) -> dict:
    """GET {base_url}/key — the account-info endpoint; HTTP 200 = key works."""
    status_code, data, err = _http_check(
        f"{settings.openrouter_base_url}/key",
        {"Authorization": f"Bearer {key}"},
    )
    if status_code == 200 and data is not None:
        info = data.get("data") if isinstance(data.get("data"), dict) else {}
        result: dict = {
            "status": "valid",
            "detail": info.get("label") or "Key accepted by OpenRouter",
        }
        if info.get("is_free_tier") is not None:
            result["info"] = {"is_free_tier": info["is_free_tier"]}
        return result
    if status_code in (401, 403):
        return {
            "status": "invalid",
            "detail": "Rejected by OpenRouter — wrong or revoked key",
        }
    if status_code:
        return {"status": "error", "detail": f"OpenRouter answered HTTP {status_code}"}
    return {"status": "error", "detail": err}


def check_tavily_key(key: str) -> dict:
    """GET https://api.tavily.com/usage — free endpoint; 200 = key works."""
    from app.services import tavily

    status_code, data, err = _http_check(
        tavily.TAVILY_USAGE_URL,
        {"Authorization": f"Bearer {key}"},
    )
    if status_code == 200 and data is not None:
        key_info = data.get("key") if isinstance(data.get("key"), dict) else {}
        account = data.get("account") if isinstance(data.get("account"), dict) else {}
        usage, limit = key_info.get("usage"), key_info.get("limit")
        result: dict = {"status": "valid", "detail": "Key accepted by Tavily"}
        info: dict = {}
        if isinstance(limit, (int, float)):
            result["detail"] = f"{usage or 0}/{limit} credits used"
            info["usage"], info["limit"] = usage, limit
        if account.get("current_plan"):
            info["plan"] = account["current_plan"]
        if info:
            result["info"] = info
        return result
    if status_code in (401, 403) or status_code == 400:
        return {
            "status": "invalid",
            "detail": "Rejected by Tavily — wrong or revoked key",
        }
    if status_code:
        return {"status": "error", "detail": f"Tavily answered HTTP {status_code}"}
    return {"status": "error", "detail": err}


def check_custom_key(key: str) -> dict:
    """GET {base_url}/models — the OpenAI-compatible model list; 200 = the
    endpoint answers and the key (if any) is accepted. Checks the whole
    custom-provider configuration, not just the key."""
    from app.services import custom_provider

    if not custom_provider.is_configured():
        return {"status": "not_set", "detail": "No custom base URL saved"}
    status_code, data, err = _http_check(
        f"{settings.custom_base_url.strip().rstrip('/')}/models",
        {"Authorization": f"Bearer {key}"} if key else {},
    )
    if status_code == 200 and data is not None:
        models = data.get("data") if isinstance(data.get("data"), list) else []
        result: dict = {"status": "valid", "detail": "Endpoint answered"}
        if models:
            result["info"] = {"models": len(models)}
        return result
    if status_code in (401, 403):
        return {
            "status": "invalid",
            "detail": "Rejected — wrong key for this endpoint",
        }
    if status_code:
        return {"status": "error", "detail": f"Endpoint answered HTTP {status_code}"}
    return {"status": "error", "detail": err}


def check_and_store(db: Session, field: str) -> dict:
    """Validate the currently saved key for `field` and persist the verdict.

    Never raises: any surprise becomes status="error" so the web UI can show
    it without breaking the settings round-trip.
    """
    if field not in CHECKED_FIELDS:
        raise ValueError(f"'{field}' has no provider status check")
    key = getattr(settings, field, "") or ""
    # The custom provider may run keyless (e.g. a local Ollama) — only its
    # base URL decides whether it is configured, so never short-circuit here.
    if not key and field != "custom_api_key":
        result = {"status": "not_set", "detail": "No key saved"}
    else:
        try:
            if field == "openrouter_api_key":
                result = check_openrouter_key(key)
            elif field == "custom_api_key":
                result = check_custom_key(key)
            else:
                result = check_tavily_key(key)
        except Exception as exc:  # defensive: a broken check must not break saving
            result = {"status": "error", "detail": f"Check failed: {exc}"}
    result["field"] = field
    result["checked_at"] = utcnow().isoformat() + "Z"
    result["checked_at_epoch"] = utcnow().timestamp()
    store_status(db, field, result)
    return result
