"""Personas (RikkaHub-style assistants): named system prompts with optional
model + temperature defaults, scoped per account.

A persona rides along with the conversation it is attached to: every
send/retry injects its system prompt (an explicit per-send system prompt
overrides it) and its temperature applies unless the request sets one.
Personas are web/UI-side for now — the phone sync contract is untouched.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Conversation, Persona, utcnow
from app.schemas import PersonaCreate, PersonaOut, PersonaUpdate
from app.security import get_account_id

router = APIRouter(prefix="/api/personas", tags=["personas"])


def _fetch_persona(db: Session, persona_id: str, account_id: str) -> Persona:
    persona = db.scalar(
        select(Persona).where(
            Persona.id == persona_id, Persona.account_id == account_id
        )
    )
    if persona is None:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@router.get("", response_model=list[PersonaOut])
def list_personas(
    db: Session = Depends(get_db), account_id: str = Depends(get_account_id)
) -> list[Persona]:
    return list(
        db.scalars(
            select(Persona)
            .where(Persona.account_id == account_id)
            .order_by(Persona.name, Persona.id)
        )
    )


@router.post("", response_model=PersonaOut, status_code=201)
def create_persona(
    payload: PersonaCreate,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> Persona:
    persona = Persona(
        id=secrets.token_hex(8),
        account_id=account_id,
        name=payload.name.strip() or "Assistant",
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=payload.temperature,
    )
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return persona


@router.patch("/{persona_id}", response_model=PersonaOut)
def update_persona(
    persona_id: str,
    payload: PersonaUpdate,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> Persona:
    persona = _fetch_persona(db, persona_id, account_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(persona, field, value)
    persona.updated_at = utcnow()
    db.commit()
    db.refresh(persona)
    return persona


@router.delete("/{persona_id}", status_code=204)
def delete_persona(
    persona_id: str,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> None:
    persona = _fetch_persona(db, persona_id, account_id)
    # Conversations keep working — they just stop riding along.
    db.execute(
        sql_update(Conversation)
        .where(
            Conversation.persona_id == persona.id,
            Conversation.account_id == account_id,
        )
        .values(persona_id=None)
    )
    db.delete(persona)
    db.commit()
