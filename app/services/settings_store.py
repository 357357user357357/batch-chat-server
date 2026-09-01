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
    "google_service_account_json",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
]
PLAIN_FIELDS = [
    "google_project_id",
    "google_location",
    "aws_region",
]
ALLOWED_FIELDS = SECRET_FIELDS + PLAIN_FIELDS


def load_overrides(db: Session) -> None:
    """Apply every saved override onto the `settings` singleton (call at startup)."""
    rows = db.scalars(select(AppSetting)).all()
    for row in rows:
        if row.key in ALLOWED_FIELDS:
            setattr(settings, row.key, row.value)


def save_overrides(db: Session, updates: dict[str, str]) -> None:
    """Persist + apply the given field updates. Unknown keys are ignored."""
    for key, value in updates.items():
        if key not in ALLOWED_FIELDS:
            continue
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        setattr(settings, key, value)
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
    updates = {k: v for k, v in data.items() if k in ALLOWED_FIELDS and isinstance(v, str)}
    save_overrides(db, updates)
