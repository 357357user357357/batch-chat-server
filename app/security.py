import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AuthToken, utcnow

bearer_scheme = HTTPBearer(auto_error=False)


def create_token(db: Session) -> AuthToken:
    token = secrets.token_urlsafe(48)
    expires_at = utcnow() + timedelta(days=settings.token_expire_days)
    auth_token = AuthToken(token=token, expires_at=expires_at)
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


def verify_password(password: str) -> bool:
    expected = settings.app_password
    return secrets.compare_digest(password, expected)