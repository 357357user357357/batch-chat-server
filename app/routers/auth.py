import base64
import html
import json
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Account, AuthToken, utcnow
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
    RegisterRequest,
)
from app.security import (
    create_token,
    get_account_id,
    get_current_token,
    verify_password,
)
from app.services import mailer
from app.services.account import (
    account_view,
    create_account as create_client_account,
    find_by_pair_code,
    rotate_account_key,
    split_pair_code,
)
from app.services.providers import configured_status

router = APIRouter(prefix="/api", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", **configured_status())


def _resolve_login_account(db: Session, payload: LoginRequest) -> Account:
    """Which account does this login target?

    - login given → that account by LABEL (case-insensitive) or by raw id
      (bc-…); 404 if unknown.
    - account_id given → that account (404 if unknown).
    - neither → the owner account: the master password IS the owner's
      credential, so a bare password login always lands there.
    """
    from app.services.account import ensure_owner_account

    wanted = (payload.login or "").strip()
    if wanted:
        # 1) by registered e-mail (exact, case-insensitive)
        by_email = db.scalar(select(Account).where(Account.email == wanted.lower()))
        if by_email is not None:
            return by_email
        rows = db.scalars(select(Account)).all()
        for account in rows:
            if (account.label or "").strip().lower() == wanted.lower():
                return account
        by_id = db.get(Account, wanted)
        if by_id is not None:
            return by_id
        raise HTTPException(status_code=404, detail="Unknown login")
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
    # E-mail-registered accounts must confirm their address before login
    # (Google sign-ins are auto-confirmed, so they never hit this).
    if account.email and not account.email_confirmed:
        raise HTTPException(
            status_code=403,
            detail="Confirm your e-mail first — check your inbox for the link",
        )
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


@router.post("/auth/register", status_code=202)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Self-service registration via e-mail: a client signs up with a unique
    e-mail + password and receives a confirmation link by mail; login works
    only after confirming. Disabled until SMTP is configured on the server."""
    if not mailer.smtp_configured():
        raise HTTPException(
            status_code=503,
            detail="E-mail registration is not configured on this server — use a pairing code",
        )
    check_login_allowed(request)
    email_addr = payload.email.strip().lower()
    if not _EMAIL_RE.match(email_addr):
        raise HTTPException(status_code=422, detail="Enter a valid e-mail address")
    if db.scalar(select(Account).where(Account.email == email_addr)) is not None:
        record_login_failure(request)
        raise HTTPException(status_code=409, detail="This e-mail is already registered")
    from app.services.account import hash_password

    label = email_addr.split("@", 1)[0][:64]
    rows = db.scalars(select(Account.label)).all()
    if any((r or "").strip().lower() == label.lower() for r in rows):
        label = email_addr[:64]  # fall back to the full address for uniqueness
    account = create_client_account(db, label=label)
    account.email = email_addr
    account.email_confirmed = False
    account.password_hash = hash_password(payload.password)
    account.confirm_token = secrets.token_urlsafe(24)
    account.confirm_token_expires = utcnow() + timedelta(hours=24)
    db.commit()
    base = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    link = f"{base}/api/auth/confirm-email?token={account.confirm_token}"
    try:
        mailer.send_message(
            email_addr,
            "Batch Chat — confirm your e-mail",
            "Welcome to Batch Chat!\n\nConfirm your e-mail by opening this link "
            f"(valid for 24 hours):\n\n{link}\n\n"
            "After confirming you can log in with this address and your password.",
        )
    except Exception:
        db.delete(account)
        db.commit()
        record_login_failure(request)
        raise HTTPException(
            status_code=502,
            detail="Could not send the confirmation e-mail — check SMTP settings",
        )
    record_login_success(request)
    return {
        "detail": "Confirmation e-mail sent — check your inbox AND the Spam "
        "folder (it can land there); the link is valid for 24 hours",
    }


@router.get("/auth/confirm-email")
def confirm_email_page(token: str, db: Session = Depends(get_db)):  # -> HTMLResponse
    """Confirmation link target: shows a confirmation page with an explicit
    button. Deliberately does NOT confirm on GET — link-preview bots
    (Telegram, messengers, mail scanners) auto-fetch GET URLs, which would
    confirm the account without the human ever seeing the mail."""
    account = db.scalar(select(Account).where(Account.confirm_token == token.strip()))
    if account is None or not account.confirm_token_expires or account.confirm_token_expires < utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired confirmation link")
    safe_token = html.escape(token.strip(), quote=True)
    html_page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Batch Chat — confirm e-mail</title>
<style>body{{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e6e6;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#1a1d24;border:1px solid #2a2e38;border-radius:12px;padding:32px;
max-width:420px;text-align:center}}
button{{margin-top:20px;padding:12px 28px;font-size:16px;border:0;border-radius:8px;
background:#4c8dff;color:#fff;cursor:pointer}}
button:hover{{background:#3a7ae8}}</style></head>
<body><div class="card"><h2>Confirm your e-mail</h2>
<p>Click the button below to confirm this address for
<strong>{html.escape(account.email or account.label or "your account", quote=True)}</strong>
and enable login with e-mail + password.</p>
<form method="post" action="/api/auth/confirm-email">
<input type="hidden" name="token" value="{safe_token}">
<button type="submit">Confirm e-mail</button></form>
<p style="margin-top:18px;font-size:13px;opacity:.6">The link is valid for 24 hours.</p>
</div></body></html>"""
    return HTMLResponse(content=html_page, status_code=200)


@router.post("/auth/confirm-email")
def confirm_email_accept(
    token: str = Form(...), db: Session = Depends(get_db)
):  # -> RedirectResponse
    """Explicit confirmation (the button on the confirmation page)."""
    account = db.scalar(select(Account).where(Account.confirm_token == token.strip()))
    if account is None or not account.confirm_token_expires or account.confirm_token_expires < utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired confirmation link")
    account.email_confirmed = True
    account.confirm_token = None
    account.confirm_token_expires = None
    db.commit()
    base = settings.public_base_url.rstrip("/")
    return RedirectResponse(url=f"{base}/#confirmed=1", status_code=302)


@router.post("/auth/resend-confirmation", status_code=202)
def resend_confirmation(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Re-send the confirmation mail for an existing, not-yet-confirmed address."""
    if not mailer.smtp_configured():
        raise HTTPException(status_code=503, detail="E-mail sending is not configured")
    check_login_allowed(request)
    email_addr = payload.email.strip().lower()
    account = db.scalar(select(Account).where(Account.email == email_addr))
    if account is None or account.email_confirmed:
        # Do not reveal whether the address exists.
        return {"detail": "If the address needs confirmation, a new mail was sent"}
    account.confirm_token = secrets.token_urlsafe(24)
    account.confirm_token_expires = utcnow() + timedelta(hours=24)
    db.commit()
    base = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    link = f"{base}/api/auth/confirm-email?token={account.confirm_token}"
    try:
        mailer.send_message(email_addr, "Batch Chat — confirm your e-mail",
                            f"Confirm your e-mail (valid 24 h):\n\n{link}\n")
    except Exception:
        raise HTTPException(status_code=502, detail="Could not send the e-mail — check SMTP settings")
    return {"detail": "Confirmation e-mail sent — check your inbox AND the Spam folder"}


# Short-lived OAuth state store (in-memory; single-process deployment).
_OAUTH_STATES: dict[str, dict] = {}


@router.get("/auth/oauth/google/start")
def google_start(request: Request, client: str = "web"):
    """Kick off the Google sign-in flow. `client=phone` redirects back to the
    app's custom scheme (batchchat://), web returns to the web UI."""
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured on this server")
    base = settings.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    state = secrets.token_urlsafe(16)
    _OAUTH_STATES[state] = {"client": client, "ts": time.time()}
    params = urlencode({
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": f"{base}/api/auth/oauth/google/callback",
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    })
    return RedirectResponse(url=f"https://accounts.google.com/o/oauth2/v2/auth?{params}", status_code=302)


@router.get("/auth/oauth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    """Exchange the OAuth code, find or create the account by e-mail
    (Google-verified addresses are auto-confirmed), hand the session token
    to the requesting client (web fragment or batchchat:// deep link)."""
    info = _OAUTH_STATES.pop(state, None)
    if not info or time.time() - info["ts"] > 600:
        raise HTTPException(status_code=400, detail="Unknown or expired OAuth state")
    base = settings.public_base_url.rstrip("/") or str(request.base.url).rstrip("/")
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": f"{base}/api/auth/oauth/google/callback",
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Google token exchange failed")
    id_token = resp.json().get("id_token", "")
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        raise HTTPException(status_code=502, detail="Malformed Google response")
    email_addr = (claims.get("email") or "").lower()
    if not email_addr or not claims.get("email_verified", False):
        raise HTTPException(status_code=401, detail="Google account has no verified e-mail")
    account = db.scalar(select(Account).where(Account.email == email_addr))
    if account is None:
        label = email_addr.split("@", 1)[0][:64]
        rows = db.scalars(select(Account.label)).all()
        if any((r or "").strip().lower() == label.lower() for r in rows):
            label = email_addr[:64]
        account = create_client_account(db, label=label)
        account.email = email_addr
    account.email_confirmed = True
    account.confirm_token = None
    account.confirm_token_expires = None
    db.commit()
    token = create_token(db, account.id)
    if info["client"] == "phone":
        return RedirectResponse(url=f"batchchat://oauth#token={token.token}", status_code=302)
    return RedirectResponse(url=f"/#token={token.token}", status_code=302)



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

    label = (payload.label or "").strip() or None
    if label:
        existing = db.scalars(select(Account.label)).all()
        if any((e or "").strip().lower() == label.lower() for e in existing):
            raise HTTPException(
                status_code=409,
                detail=f"Label '{label}' is already taken — labels must be unique",
            )

    from app.services.account import hash_password

    account = create_client_account(db, label=label)
    account.password_hash = hash_password(payload.client_password)
    db.commit()
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
        AccountSummary(
            account_id=a.id,
            label=a.label,
            has_password=bool(a.password_hash),
            created_at=a.created_at,
        )
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
def me(account_id: str = Depends(get_account_id), db: Session = Depends(get_db)) -> dict:
    from app.services.account import ensure_owner_account

    return {
        "ok": True,
        "account_id": account_id,
        "is_owner": account_id == ensure_owner_account(db).id,
    }


@router.delete("/auth/account")
def delete_own_account(
    account_id: str = Depends(get_account_id),
    db: Session = Depends(get_db),
) -> dict:
    """Self-service account deletion.

    Permanently removes the caller's account with ALL its data: dialogs,
    messages, tombstones, batch jobs/items and every session token (the
    current one included — the caller is logged out everywhere). The e-mail
    and login are freed, so the same address can register again from zero.
    The instance owner account cannot be deleted: it carries the provider
    keys and the master-password identity.
    """
    from app.models import BatchItem, BatchJob, Conversation, Message, MessageTombstone
    from app.services.account import ensure_owner_account

    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Unknown account")
    if account.id == ensure_owner_account(db).id:
        raise HTTPException(
            status_code=403,
            detail="The owner account cannot be deleted — it manages this server",
        )

    conv_ids = db.scalars(
        select(Conversation.id).where(Conversation.account_id == account.id)
    ).all()
    if conv_ids:
        job_ids = db.scalars(
            select(BatchJob.id).where(
                BatchJob.account_id == account.id,
                BatchJob.conversation_id.in_(conv_ids),
            )
        ).all()
        if job_ids:
            db.execute(delete(BatchItem).where(BatchItem.batch_job_id.in_(job_ids)))
        db.execute(delete(BatchJob).where(BatchJob.conversation_id.in_(conv_ids)))
        db.execute(
            delete(MessageTombstone).where(MessageTombstone.conversation_id.in_(conv_ids))
        )
        db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
        db.execute(delete(Conversation).where(Conversation.id.in_(conv_ids)))
    # Any account-scoped batches without a conversation (defensive).
    other_job_ids = db.scalars(
        select(BatchJob.id).where(BatchJob.account_id == account.id)
    ).all()
    if other_job_ids:
        db.execute(delete(BatchItem).where(BatchItem.batch_job_id.in_(other_job_ids)))
        db.execute(delete(BatchJob).where(BatchJob.id.in_(other_job_ids)))
    db.execute(delete(AuthToken).where(AuthToken.account_id == account.id))
    db.delete(account)
    db.commit()
    return {"ok": True, "deleted_dialogs": len(conv_ids)}