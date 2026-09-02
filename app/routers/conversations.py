from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import AuthToken, Conversation, Message, utcnow
from app.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationRename,
    ConversationSummary,
    MessageCreate,
    MessageOut,
)
from app.security import get_current_token

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSummary])
def list_conversations(db: Session = Depends(get_db), _: AuthToken = Depends(get_current_token)):
    rows = db.execute(
        select(
            Conversation,
            func.count(Message.id),
            func.max(Message.content),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.id)
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
    return ConversationDetail.model_validate(conv)


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


def _fetch_conversation(db: Session, conversation_id: int) -> Conversation:
    conv = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv