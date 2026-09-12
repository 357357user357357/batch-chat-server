import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import OwnerEmailUpdate, SettingsBackup, SettingsUpdate
from app.security import get_current_token, get_owner_account_id

# OWNER-ONLY router: every endpoint requires the instance owner account (the
# first account). Secondary client accounts get 403 — they must never read
# or change the server's provider credentials.
from app.services.key_status import CHECKED_FIELDS, check_and_store, clear_status
from app.services.settings_store import current_view, export_backup, import_backup, save_overrides

router = APIRouter(prefix="/api/settings", tags=["settings"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("")
def get_settings(
    db: Session = Depends(get_db),
    _account_id: str = Depends(get_owner_account_id),
) -> dict:
    """UI-safe snapshot of provider credentials (secrets masked, key status
    included for OpenRouter/Tavily)."""
    return current_view(db)


@router.put("")
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    _account_id: str = Depends(get_owner_account_id),
) -> dict:
    """Save provider credentials and apply them immediately (no restart).

    A newly pasted OpenRouter/Tavily key is validated against its provider
    right away, so the UI can show the verdict without a second click."""
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    save_overrides(db, updates)
    checked: dict = {}
    for field in CHECKED_FIELDS:
        if field in updates:
            checked[field] = check_and_store(db, field)
    view = current_view(db)
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
    _account_id: str = Depends(get_owner_account_id),
) -> dict:
    """Remove a saved key (and its stored status) from the server."""
    if field not in CHECKED_FIELDS:
        raise HTTPException(status_code=404, detail=f"'{field}' cannot be deleted here")
    save_overrides(db, {field: ""})
    clear_status(db, field)
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
