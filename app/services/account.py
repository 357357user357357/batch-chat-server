"""Client accounts for the batch-chat instance.

Multi-account: the instance can hold several isolated clients. Every account
has a stable id (`bc-…`, public), a secret pair key (the pairing-code half),
an optional own password, and its own data — dialogs/messages/batches are
stamped with `account_id` and every API query is filtered by the token's
account. The FIRST account is the instance owner (migrated from the legacy
app_settings identity, same id and key, so existing pair codes keep working):
only the owner manages provider credentials.

Accounts survive IP changes and server moves: copy the pair code to any new
device and it pairs into the SAME account and history.
"""

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, AppSetting

LEGACY_ID_ROW = "account_id"
LEGACY_KEY_ROW = "account_key"


def _generate_account_id() -> str:
    # Public identifier: short enough to read aloud, long enough to be unique.
    return f"bc-{secrets.token_hex(5)}"


def _generate_account_key() -> str:
    # The actual secret — full token_urlsafe entropy.
    return secrets.token_urlsafe(24)


# --------------------------------------------------------------- passwords
# pbkdf2 (stdlib, no extra dependency) for optional per-account passwords.


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password_hash(password: str, stored: str) -> bool:
    try:
        _scheme, salt, expected = stored.split("$", 2)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return secrets.compare_digest(digest.hex(), expected)


# --------------------------------------------------------------- lifecycle


def ensure_owner_account(db: Session) -> Account:
    """Return the owner (first) account; migrate the legacy single-instance
    identity from app_settings on first run so the id, key and every already
    copied pair code stay valid."""
    owner = db.scalar(select(Account).order_by(Account.created_at, Account.id).limit(1))
    if owner is not None:
        return owner

    legacy_id = db.get(AppSetting, LEGACY_ID_ROW)
    legacy_key = db.get(AppSetting, LEGACY_KEY_ROW)
    owner = Account(
        id=(legacy_id.value if legacy_id and legacy_id.value else _generate_account_id()),
        key=(legacy_key.value if legacy_key and legacy_key.value else _generate_account_key()),
        label="owner",
    )
    db.add(owner)
    db.commit()
    return owner


def default_account_id(db: Session) -> str:
    return ensure_owner_account(db).id


def get_account(db: Session, account_id: str) -> Account | None:
    return db.get(Account, account_id)


def find_by_pair_code(db: Session, account_id: str, account_key: str) -> Account | None:
    """Constant-time-ish pair-code check: the id is looked up, then the key
    compared with compare_digest (no early-exit on key mismatch content)."""
    account = db.get(Account, account_id)
    if account is None:
        # Burn comparable time so unknown ids aren't distinguishable.
        secrets.compare_digest(account_key, "")
        return None
    if secrets.compare_digest(account_key, account.key):
        return account
    return None


def create_account(db: Session, label: str | None = None) -> Account:
    """Create an additional isolated client account (admin/master only)."""
    account = Account(id=_generate_account_id(), key=_generate_account_key(), label=label)
    db.add(account)
    db.commit()
    return account


def rotate_account_key(db: Session, account_id: str) -> str:
    """Issue a fresh pair key (id stays stable). Old pair codes for this
    account stop working; already-paired devices keep their session tokens."""
    account = db.get(Account, account_id)
    if account is None:
        raise ValueError("unknown account")
    account.key = _generate_account_key()
    db.commit()
    return account.key


# --------------------------------------------------------------- pair codes


def build_pair_code(account_id: str, account_key: str, server_url: str = "") -> str:
    """One copyable string: optional server origin + id + key.

    A phone that pastes this fills in the server address automatically and
    pairs without ever seeing a password.
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


def account_view(account: Account, server_url: str = "") -> dict:
    return {
        "account_id": account.id,
        "account_key": account.key,
        "pair_code": build_pair_code(account.id, account.key, server_url),
    }