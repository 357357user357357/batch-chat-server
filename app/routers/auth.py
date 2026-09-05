from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuthToken
from app.rate_limit import (
    check_login_allowed,
    record_login_failure,
    record_login_success,
)
from app.schemas import (
    HealthResponse,
    LoginRequest,
    LoginResponse,
)
from app.security import create_token, get_current_token, verify_password
from app.services.providers import configured_status

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", **configured_status())


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    # Brute-force protection: fail-2-lockout per client IP.
    check_login_allowed(request)
    if not verify_password(payload.password):
        record_login_failure(request)
        raise HTTPException(status_code=401, detail="Wrong password")
    record_login_success(request)
    token = create_token(db)
    return LoginResponse(token=token.token, expires_at=token.expires_at)


@router.post("/auth/logout")
def logout(
    token: AuthToken = Depends(get_current_token),
    db: Session = Depends(get_db),
) -> dict:
    db.delete(token)
    db.commit()
    return {"ok": True}


@router.get("/auth/me")
def me(_token: AuthToken = Depends(get_current_token)) -> dict:
    return {"ok": True}