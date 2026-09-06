import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Account, AuthToken
from app.rate_limit import (
    check_login_allowed,
    record_login_failure,
    record_login_success,
)
from app.schemas import (
    AccountCreateRequest,
    AccountResponse,
    AccountSummary,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    PairRequest,
)
from app.security import (
    create_token,
    get_account_id,
    get_current_token,
    verify_password,
)
from app.services.account import (
    account_view,
    create_account as create_client_account,
    find_by_pair_code,
    rotate_account_key,
    split_pair_code,
)
from app.services.providers import configured_status

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", **configured_status())


def _resolve_login_account(db: Session, payload: LoginRequest) -> Account:
    """Which account does this login target?

    - account_id given → that account (404 if unknown).
    - no account_id    → the owner account: the master password IS the
      owner's credential, so a bare password login always lands there —
      even after secondary client accounts exist.
    """
    from app.services.account import ensure_owner_account

    if payload.account_id:
        account = db.get(Account, payload.account_id.strip())
        if account is None:
            raise HTTPException(status_code=404, detail="Unknown account id")
        return account
    return ensure_owner_account(db)


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    # Brute-force protection: fail-2-lockout per client IP.
    check_login_allowed(request)
    account = _resolve_login_account(db, payload)
    if not verify_password(payload.password, account):
        record_login_failure(request)
        raise HTTPException(status_code=401, detail="Wrong password")
    record_login_success(request)
    token = create_token(db, account.id)
    return LoginResponse(token=token.token, expires_at=token.expires_at)


@router.post("/auth/pair", response_model=LoginResponse)
def pair(payload: PairRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    """Pair a device with ONE account via its pairing code.

    The pasted code (`account_id|account_key`, optionally prefixed with the
    server origin) selects the account — the resulting session token is
    bound to it and sees only that account's dialogs, messages and batches.
    Brute-force protected the same way as the password login.
    """
    check_login_allowed(request)
    parsed = split_pair_code(payload.code)
    account = find_by_pair_code(db, *(p.strip() for p in parsed)) if parsed else None
    if account is None:
        record_login_failure(request)
        raise HTTPException(status_code=401, detail="Invalid pairing code")
    record_login_success(request)
    token = create_token(db, account.id)
    return LoginResponse(token=token.token, expires_at=token.expires_at)


@router.get("/auth/account", response_model=AccountResponse)
def account(
    request: Request,
    account_id: str = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AccountResponse:
    """The current session's account identity + copyable pairing code."""
    acct = db.get(Account, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    base = str(request.base_url).rstrip("/")
    return AccountResponse(**account_view(acct, server_url=base))


@router.post("/auth/account/rotate", response_model=AccountResponse)
def rotate_account(
    request: Request,
    account_id: str = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> AccountResponse:
    """Issue a fresh pair key for THIS session's account. Old pairing codes
    for it stop working; devices that already paired keep their sessions."""
    rotate_account_key(db, account_id)
    acct = db.get(Account, account_id)
    base = str(request.base_url).rstrip("/")
    return AccountResponse(**account_view(acct, server_url=base))


@router.post("/auth/accounts", response_model=AccountResponse, status_code=201)
def create_client(
    payload: AccountCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AccountResponse:
    """Create an additional isolated client account (owner/admin action).

    Authenticated with the instance master password: the person running the
    server hands the returned pairing code to the new client, whose session
    then sees ONLY its own dialogs — never the owner's data or the provider
    credentials.
    """
    check_login_allowed(request)
    if not secrets.compare_digest(payload.admin_password, settings.app_password):
        record_login_failure(request)
        raise HTTPException(status_code=401, detail="Wrong admin password")
    record_login_success(request)
    account = create_client_account(db, label=payload.label)
    base = str(request.base_url).rstrip("/")
    return AccountResponse(**account_view(account, server_url=base))


@router.get("/auth/accounts", response_model=list[AccountSummary])
def list_accounts(
    _account_id: str = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> list[AccountSummary]:
    """All accounts on this instance (ids + labels only, no secrets)."""
    rows = db.scalars(select(Account).order_by(Account.created_at, Account.id)).all()
    return [
        AccountSummary(account_id=a.id, label=a.label, created_at=a.created_at)
        for a in rows
    ]


@router.post("/auth/logout")
def logout(
    token: AuthToken = Depends(get_current_token),
    db: Session = Depends(get_db),
) -> dict:
    db.delete(token)
    db.commit()
    return {"ok": True}


@router.get("/auth/me")
def me(account_id: str = Depends(get_account_id)) -> dict:
    return {"ok": True, "account_id": account_id}