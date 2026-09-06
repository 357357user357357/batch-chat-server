import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Account, AuthToken, utcnow

bearer_scheme = HTTPBearer(auto_error=False)


def create_token(db: Session, account_id: str) -> AuthToken:
    token = secrets.token_urlsafe(48)
    expires_at = utcnow() + timedelta(days=settings.token_expire_days)
    auth_token = AuthToken(token=token, expires_at=expires_at, account_id=account_id)
    db.add(auth_token)
    db.commit()
    db.refresh(auth_token)
    return auth_token


def get_current_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthToken:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    auth_token = db.get(AuthToken, credentials.credentials)
    if auth_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    if auth_token.expires_at < utcnow():
        db.delete(auth_token)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    return auth_token


def get_account_id(
    token: AuthToken = Depends(get_current_token),
    db: Session = Depends(get_db),
) -> str:
    """The account this session's data belongs to.

    Every data query in every router filters by this value — that is the
    whole multi-account isolation: a token issued for account A can never
    read or write account B's dialogs, messages or batches. Legacy tokens
    created before accounts existed are bound to the owner account on
    first use.
    """
    from app.services.account import default_account_id

    if token.account_id:
        return token.account_id
    # Legacy token (pre-multi-account): bind it to the owner account.
    owner_id = default_account_id(db)
    token.account_id = owner_id
    db.commit()
    return owner_id


def get_owner_account_id(
    account_id: str = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> str:
    """Owner-only dependency: the first (migrated) account manages provider
    credentials and account administration; secondary client accounts get 403."""
    from app.services.account import default_account_id

    owner_id = default_account_id(db)
    if account_id != owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner account required",
        )
    return account_id


def verify_password(password: str, account: Account | None = None) -> bool:
    """Check a login password.

    If the target account has its own password hash, that hash decides.
    Otherwise the instance master password (settings.app_password) is
    accepted for every account.
    """
    from app.services.account import verify_password_hash

    if account is not None and account.password_hash:
        return verify_password_hash(password, account.password_hash)
    expected = settings.app_password
    return secrets.compare_digest(password, expected)