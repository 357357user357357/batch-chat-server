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
    AccountResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    PairRequest,
)
from app.security import create_token, get_current_token, verify_password
from app.services.account import account_view, rotate_account_key, verify_pair_code
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


@router.post("/auth/pair", response_model=LoginResponse)
def pair(payload: PairRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    """Pair a device with the instance's portable account identity.

    The pasted pairing code (`account_id|account_key`, optionally prefixed
    with the server origin) replaces password sharing: a new device obtains
    a normal session token and immediately syncs the whole history. Brute-
    force protected the same way as the password login.
    """
    check_login_allowed(request)
    if not verify_pair_code(db, payload.code):
        record_login_failure(request)
        raise HTTPException(status_code=401, detail="Invalid pairing code")
    record_login_success(request)
    token = create_token(db)
    return LoginResponse(token=token.token, expires_at=token.expires_at)


@router.get("/auth/account", response_model=AccountResponse)
def account(
    request: Request,
    _token: AuthToken = Depends(get_current_token),
    db: Session = Depends(get_db),
) -> AccountResponse:
    """This instance's portable identity + copyable pairing code."""
    base = str(request.base_url).rstrip("/")
    return AccountResponse(**account_view(db, server_url=base))


@router.post("/auth/account/rotate", response_model=AccountResponse)
def rotate_account(
    request: Request,
    _token: AuthToken = Depends(get_current_token),
    db: Session = Depends(get_db),
) -> AccountResponse:
    """Issue a fresh account_key. Old pairing codes stop working; devices
    that already paired keep their sessions (their tokens are untouched)."""
    rotate_account_key(db)
    base = str(request.base_url).rstrip("/")
    return AccountResponse(**account_view(db, server_url=base))


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