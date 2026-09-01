from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuthToken
from app.schemas import SettingsUpdate
from app.security import get_current_token
from app.services.settings_store import current_view, save_overrides

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(
    _: AuthToken = Depends(get_current_token),
) -> dict:
    """UI-safe snapshot of provider credentials (secrets masked)."""
    return current_view()


@router.put("")
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> dict:
    """Save provider credentials and apply them immediately (no restart)."""
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    save_overrides(db, updates)
    return current_view()
