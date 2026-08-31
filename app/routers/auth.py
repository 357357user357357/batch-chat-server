from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AuthToken
from app.schemas import (
    HealthResponse,
    LoginRequest,
    LoginResponse,
)
from app.security import create_token, get_current_token, verify_password

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        openrouter_configured=bool(settings.openrouter_api_key),
    )


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    if not verify_password(payload.password):
        raise HTTPException(status_code=401, detail="Wrong password")
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