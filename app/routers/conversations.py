import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import AppSetting, AuthToken, Conversation, Message, MessageTombstone, utcnow
from app.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationRename,
    ConversationSummary,
    KeepaliveToggle,
    MessageCreate,
    MessageOut,
)
from app.security import get_current_token
from app.services import cache_keeper

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSummary])
def list_conversations(db: Session = Depends(get_db), _: AuthToken = Depends(get_current_token)):
    rows = db.execute(
        select(
            Conversation,
            func.count(Message.id),
            func.max(Message.content),
        )
        .outerjoin(
            Message,
            and_(
                Message.conversation_id == Conversation.id,
                Message.deleted_at.is_(None),
            ),
        )
        .where(Conversation.deleted_at.is_(None))
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc())
    ).all()

    result = []
    for conv, count, last in rows:
        result.append(
            ConversationSummary(
                id=conv.id,
                kind=conv.kind,
                model=conv.model,
                title=conv.title,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                message_count=count or 0,
                last_message=last,
            )
        )
    return result


@router.post("", response_model=ConversationDetail, status_code=201)
def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> ConversationDetail:
    conv = Conversation(title=payload.title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationDetail.model_validate(conv)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> ConversationDetail:
    conv = _fetch_conversation(db, conversation_id)
    detail = ConversationDetail.model_validate(conv)
    detail.keepalive = bool(conv.keepalive_enabled)
    # Hide individually deleted Q/A (they stay archived in the DB only).
    live_ids = {
        m.id for m in conv.messages if m.deleted_at is None
    }
    detail.messages = [m for m in detail.messages if m.id in live_ids]
    return detail


@router.patch("/{conversation_id}", response_model=ConversationDetail)
def rename_conversation(
    conversation_id: int,
    payload: ConversationRename,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> ConversationDetail:
    conv = _fetch_conversation(db, conversation_id)
    conv.title = payload.title
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(conv)
    return ConversationDetail.model_validate(conv)


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> None:
    # Soft delete (tombstone) without wiping the messages: other synced
    # devices learn about the deletion the next time they pull (and drop their
    # copy), while the correspondence itself stays archived in the DB.
    conv = _fetch_conversation(db, conversation_id)
    conv.deleted_at = utcnow()
    conv.updated_at = utcnow()
    db.commit()


@router.delete("/{conversation_id}/messages/{message_id}", status_code=204)
def delete_message(
    conversation_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> None:
    """Delete one question/answer inside a dialogue (web UI).

    Soft delete: the text stays archived in the DB (`messages.deleted_at`),
    and a tombstone records the removal so a later phone push (which always
    uploads the dialog's full local message list) cannot resurrect it.
    Other devices drop the message on their next sync pull.
    """
    conv = _fetch_conversation(db, conversation_id)
    msg = db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conv.id,
            Message.deleted_at.is_(None),
        )
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")

    msg.deleted_at = utcnow()
    db.add(
        MessageTombstone(
            conversation_id=conv.id, role=msg.role, content=msg.content
        )
    )
    conv.updated_at = utcnow()
    db.commit()


@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=201)
def add_message(
    conversation_id: int,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> MessageOut:
    _fetch_conversation(db, conversation_id)
    msg = Message(
        conversation_id=conversation_id,
        role=payload.role,
        content=payload.content,
        model=payload.model,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return MessageOut.model_validate(msg)


@router.post("/{conversation_id}/keepalive")
def toggle_keepalive(
    conversation_id: int,
    payload: KeepaliveToggle,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> dict:
    """🔥 Cache toggle: enable/disable prompt-cache warm-up for one dialog.

    When enabled, the background keeper pings this dialog's cached prefix
    (near-empty requests every 45 min) for the configured keep-alive window.
    The flag is persisted, so it survives server restarts.
    """
    conv = _fetch_conversation(db, conversation_id)
    conv.keepalive_enabled = payload.enabled
    conv.updated_at = utcnow()
    cache_keeper.set_enabled(conv.id, payload.enabled)

    # Persist the enabled set so it survives restarts.
    ids = [
        row[0]
        for row in db.execute(
            select(Conversation.id).where(Conversation.keepalive_enabled.is_(True))
        ).all()
        if row[0] != conv.id or payload.enabled
    ]
    if payload.enabled and conv.id not in ids:
        ids.append(conv.id)
    row = db.get(AppSetting, "keepalive_conversation_ids")
    if row is None:
        row = AppSetting(key="keepalive_conversation_ids", value="")
        db.add(row)
    row.value = json.dumps(sorted(set(ids)))
    db.commit()
    return {"ok": True, "keepalive": conv.keepalive_enabled}


def _fetch_conversation(db: Session, conversation_id: int) -> Conversation:
    conv = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv