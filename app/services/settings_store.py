"""DB-backed overrides for provider credentials, editable from the web UI.

Values saved here take priority over `.env` and apply immediately (no
container restart needed) by mutating the shared `settings` singleton.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting

# Fields that may be edited from the web UI. Keep this in sync with
# app.config.Settings — every key here must be a real attribute on `settings`.
SECRET_FIELDS = [
    "openrouter_api_key",
    "tavily_api_key",
    "google_service_account_json",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
]
PLAIN_FIELDS = [
    "google_project_id",
    "google_location",
    "aws_region",
    "cache_duration_seconds",
    "cache_keepalive_hours",
]
ALLOWED_FIELDS = SECRET_FIELDS + PLAIN_FIELDS

# Plain fields whose value is an integer (stored as text in the DB, but kept as
# an int on the `settings` singleton and returned as an int to the UI).
INT_FIELDS = {"cache_duration_seconds", "cache_keepalive_hours"}


def _coerce(key: str, value):
    if key in INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return value


def load_overrides(db: Session) -> None:
    """Apply every saved override onto the `settings` singleton (call at startup)."""
    rows = db.scalars(select(AppSetting)).all()
    for row in rows:
        if row.key in ALLOWED_FIELDS:
            setattr(settings, row.key, _coerce(row.key, row.value))


def save_overrides(db: Session, updates: dict[str, str]) -> None:
    """Persist + apply the given field updates. Unknown keys are ignored."""
    for key, value in updates.items():
        if key not in ALLOWED_FIELDS:
            continue
        row = db.get(AppSetting, key)
        stored = "" if value is None else str(value)
        if row is None:
            row = AppSetting(key=key, value=stored)
            db.add(row)
        else:
            row.value = stored
        setattr(settings, key, _coerce(key, value))
    db.commit()


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def current_view() -> dict:
    """UI-safe snapshot: secrets are masked, plain fields shown as-is."""
    view: dict = {}
    for field in SECRET_FIELDS:
        value = getattr(settings, field, "") or ""
        view[field] = {"configured": bool(value), "hint": _mask(value)}
    for field in PLAIN_FIELDS:
        view[field] = {"configured": bool(getattr(settings, field, "")), "value": getattr(settings, field, "")}
    return view


def export_backup() -> dict:
    """Raw (unmasked) snapshot of every saved credential, for the "download a
    backup file before shutting down the old server" migration flow."""
    return {field: getattr(settings, field, "") or "" for field in ALLOWED_FIELDS}


def import_backup(db: Session, data: dict) -> None:
    """Restore credentials from a file produced by `export_backup`. Unknown
    keys are ignored, missing keys are left untouched (partial backups ok)."""
    updates: dict = {}
    for key, value in data.items():
        if key not in ALLOWED_FIELDS:
            continue
        if key in INT_FIELDS:
            if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
                updates[key] = value
        elif isinstance(value, str):
            updates[key] = value
    save_overrides(db, updates)


# Secrets shared with synced devices. The Android app holds its own set of
# provider keys (OpenRouter, Tavily) in its SecureStore; both sides fill in
# anything the other is missing so every device ends up with one shared set.
SYNCABLE_KEY_FIELDS = ["openrouter_api_key", "tavily_api_key"]


def syncable_keys() -> dict[str, str]:
    """The server's current values for keys that may be synced to devices."""
    return {
        field: (getattr(settings, field, "") or "")
        for field in SYNCABLE_KEY_FIELDS
    }


def adopt_missing_keys(db: Session, keys: dict[str, str] | None) -> None:
    """Adopt provider keys a synced device provided that this server lacks.

    Server-first: an existing server value is never overwritten — the device
    only fills gaps. Persisted + applied immediately (no restart needed).
    """
    if not keys:
        return
    updates: dict[str, str] = {}
    for field in SYNCABLE_KEY_FIELDS:
        incoming = keys.get(field)
        if incoming and not getattr(settings, field, ""):
            updates[field] = incoming
    if updates:
        save_overrides(db, updates)
