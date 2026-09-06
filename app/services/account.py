"""Portable account identity for the batch-chat instance.

The account survives IP changes and server moves: `account_id` names this
instance, `account_key` is the pairing secret. A device that receives the
combined *pairing code* (`account_id|account_key`) can obtain a normal
session token via POST /api/auth/pair and immediately sync the whole
history — no password sharing needed. Both values live in the app_settings
table, so they survive restarts, container rebuilds and DB backups.
"""

import secrets

from sqlalchemy.orm import Session

from app.models import AppSetting

ACCOUNT_ID_ROW = "account_id"
ACCOUNT_KEY_ROW = "account_key"


def _generate_account_id() -> str:
    # Public identifier: short enough to read aloud, long enough to be unique.
    return f"bc-{secrets.token_hex(5)}"


def _generate_account_key() -> str:
    # The actual secret — full token_urlsafe entropy.
    return secrets.token_urlsafe(24)


def ensure_account(db: Session) -> tuple[str, str]:
    """Create the account identity on first start; keep it stable afterwards."""
    id_row = db.get(AppSetting, ACCOUNT_ID_ROW)
    key_row = db.get(AppSetting, ACCOUNT_KEY_ROW)
    account_id = id_row.value if id_row and id_row.value else None
    account_key = key_row.value if key_row and key_row.value else None
    changed = False
    if not account_id:
        account_id = _generate_account_id()
        db.add(AppSetting(key=ACCOUNT_ID_ROW, value=account_id))
        changed = True
    if not account_key:
        account_key = _generate_account_key()
        db.add(AppSetting(key=ACCOUNT_KEY_ROW, value=account_key))
        changed = True
    if changed:
        db.commit()
    return account_id, account_key


def get_account(db: Session) -> tuple[str, str]:
    return ensure_account(db)


def build_pair_code(account_id: str, account_key: str, server_url: str = "") -> str:
    """One copyable string: optional server origin + id + key.

    A phone that pastes this fills in the server address automatically and
    pairs without ever seeing the login password.
    """
    parts = [server_url.rstrip("/"), account_id, account_key] if server_url else [
        account_id,
        account_key,
    ]
    return "|".join(parts)


def split_pair_code(code: str) -> tuple[str, str] | None:
    """Extract (account_id, account_key) from a pasted pairing code."""
    parts = [p for p in code.strip().split("|") if p]
    if len(parts) == 3:
        return parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


def verify_pair_code(db: Session, code: str) -> bool:
    parsed = split_pair_code(code)
    if parsed is None:
        return False
    account_id, account_key = get_account(db)
    id_ok = secrets.compare_digest(parsed[0].strip(), account_id)
    key_ok = secrets.compare_digest(parsed[1].strip(), account_key)
    return id_ok and key_ok


def rotate_account_key(db: Session) -> str:
    """Issue a fresh account_key (id stays stable). Old pair codes stop
    working; already-paired devices keep their session tokens."""
    new_key = _generate_account_key()
    row = db.get(AppSetting, ACCOUNT_KEY_ROW)
    if row is None:
        db.add(AppSetting(key=ACCOUNT_KEY_ROW, value=new_key))
    else:
        row.value = new_key
    db.commit()
    return new_key


def account_view(db: Session, server_url: str = "") -> dict:
    account_id, account_key = get_account(db)
    return {
        "account_id": account_id,
        "account_key": account_key,
        "pair_code": build_pair_code(account_id, account_key, server_url),
    }