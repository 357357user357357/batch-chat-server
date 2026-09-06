from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import SettingsBackup, SettingsUpdate
from app.security import get_current_token, get_owner_account_id

# OWNER-ONLY router: every endpoint requires the instance owner account (the
# first account). Secondary client accounts get 403 — they must never read
# or change the server's provider credentials.
from app.services.settings_store import current_view, export_backup, import_backup, save_overrides

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(
    _account_id: str = Depends(get_owner_account_id),
) -> dict:
    """UI-safe snapshot of provider credentials (secrets masked)."""
    return current_view()


@router.put("")
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    _account_id: str = Depends(get_owner_account_id),
) -> dict:
    """Save provider credentials and apply them immediately (no restart)."""
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    save_overrides(db, updates)
    return current_view()


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
    return current_view()
