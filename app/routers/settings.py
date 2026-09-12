import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import OwnerEmailUpdate, SettingsBackup, SettingsUpdate
from app.security import get_account_id, get_owner_account_id

# OWNER-ONLY router for the server infrastructure (Google/AWS/cache tuning,
# backups, owner e-mail). The two provider keys are the exception: they are
# the instance-wide chat/search credentials (synced with paired devices), so
# every signed-in account may view their masked value + status, replace them
# or delete them — that is the whole client-facing settings surface.
from app.services.key_status import CHECKED_FIELDS, check_and_store, clear_status
from app.services.settings_store import current_view, export_backup, import_backup, save_overrides

router = APIRouter(prefix="/api/settings", tags=["settings"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SHARED_KEY_FIELDS = ["openrouter_api_key", "custom_api_key", "tavily_api_key"]


def _shared_view(db: Session) -> dict:
    """Client-safe snapshot: just the shared provider keys (masked + status)."""
    view = current_view(db)
    return {field: view[field] for field in SHARED_KEY_FIELDS}


def _is_owner(account_id: str, db: Session) -> bool:
    from app.services.account import default_account_id

    return account_id == default_account_id(db)


@router.get("")
def get_settings(
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> dict:
    """UI-safe snapshot of provider credentials (secrets masked, key status
    included for OpenRouter/Tavily). Clients get only the two shared keys."""
    if not _is_owner(account_id, db):
        return _shared_view(db)
    return current_view(db)


@router.put("")
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> dict:
    """Save provider credentials and apply them immediately (no restart).

    A newly pasted OpenRouter/Tavily key is validated against its provider
    right away, so the UI can show the verdict without a second click."""
    owner = _is_owner(account_id, db)
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "custom_base_url" in updates and updates["custom_base_url"]:
        # Normalize once here so API/backup paths match what the web UI saves.
        updates["custom_base_url"] = updates["custom_base_url"].strip().rstrip("/")
        if not updates["custom_base_url"]:
            del updates["custom_base_url"]
    if not owner:
        if any(field not in SHARED_KEY_FIELDS for field in updates):
            raise HTTPException(
                status_code=403,
                detail="Owner account required",
            )
        if not updates:
            return _shared_view(db)
    save_overrides(db, updates)
    checked: dict = {}
    for field in CHECKED_FIELDS:
        if field in updates:
            checked[field] = check_and_store(db, field)
    if owner and "custom_api_key" not in checked and (
        "custom_base_url" in updates or "custom_default_model" in updates
    ):
        # A pasted custom base URL / default model re-checks the endpoint too.
        checked["custom_api_key"] = check_and_store(db, "custom_api_key")
    view = current_view(db) if owner else _shared_view(db)
    if checked:
        view["checked_keys"] = checked
    return view


@router.post("/keys/{field}/check")
def check_key(
    field: str,
    db: Session = Depends(get_db),
    _account_id: str = Depends(get_owner_account_id),
) -> dict:
    """Re-validate the saved key against its provider and store the verdict."""
    if field not in CHECKED_FIELDS:
        raise HTTPException(status_code=404, detail=f"'{field}' has no status check")
    return check_and_store(db, field)


@router.delete("/keys/{field}")
def delete_key(
    field: str,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> dict:
    """Remove a saved key (and its stored status) from the server."""
    if field not in CHECKED_FIELDS:
        raise HTTPException(status_code=404, detail=f"'{field}' cannot be deleted here")
    save_overrides(db, {field: ""})
    clear_status(db, field)
    if not _is_owner(account_id, db):
        return _shared_view(db)
    return current_view(db)


@router.post("/owner-email")
def set_owner_email(
    payload: OwnerEmailUpdate,
    db: Session = Depends(get_db),
    _account_id: str = Depends(get_owner_account_id),
) -> dict:
    """Bind an e-mail address to the owner account.

    Afterwards, signing in with that address (e-mail + master password, or
    Google) logs into the owner account — the one that manages provider
    credentials. Useful when the owner prefers their e-mail/Google identity
    over the bare master-password login."""
    from app.services.account import bind_owner_email

    email = payload.email.strip().lower()
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid e-mail address")
    bind_owner_email(db, email)
    return {"ok": True, "owner_email": email}


@router.get("/backup", response_model=SettingsBackup)
def download_backup(
    _account_id: str = Depends(get_owner_account_id),
) -> SettingsBackup:
    """Unmasked credential export — download before retiring a server, then
    restore it on the replacement with POST /api/settings/backup."""
    return SettingsBackup(**export_backup())


@router.post("/backup")
def restore_backup(
    payload: SettingsBackup,
    db: Session = Depends(get_db),
    _account_id: str = Depends(get_owner_account_id),
) -> dict:
    """Restore credentials from a backup file produced by the GET above."""
    import_backup(db, payload.model_dump())
    return current_view(db)
