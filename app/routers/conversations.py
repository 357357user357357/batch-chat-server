import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.device import device_label
from app.models import AppSetting, Conversation, Message, MessageTombstone, utcnow
from app.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationRename,
    ConversationSummary,
    KeepaliveToggle,
    MessageCreate,
    MessageOut,
)
from app.security import get_account_id, get_current_token
from app.services import cache_keeper

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> list[ConversationSummary]:
    # Data isolation: only the calling account's dialogs. Messages/tombstones
    # inherit the scope through conversation_id.
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
        .where(
            Conversation.deleted_at.is_(None),
            Conversation.account_id == account_id,
        )
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
    request: Request,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> ConversationDetail:
    conv = Conversation(title=payload.title, account_id=account_id)
    # Audit trail: whose record it is (and the web/modification date is
    # updated_at, maintained automatically).
    device = device_label(request)
    conv.origin_device = device
    conv.modified_by = device
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationDetail.model_validate(conv)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> ConversationDetail:
    conv = _fetch_conversation(db, conversation_id, account_id)
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
    account_id: str = Depends(get_account_id),
) -> ConversationDetail:
    conv = _fetch_conversation(db, conversation_id, account_id)
    conv.title = payload.title
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(conv)
    return ConversationDetail.model_validate(conv)


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> None:
    # Soft delete (tombstone) without wiping the messages: other synced
    # devices learn about the deletion the next time they pull (and drop their
    # copy), while the correspondence itself stays archived in the DB.
    conv = _fetch_conversation(db, conversation_id, account_id)
    conv.deleted_at = utcnow()
    conv.updated_at = utcnow()
    conv.deleted_by = device_label(request)
    db.commit()


@router.delete("/{conversation_id}/messages/{message_id}", status_code=204)
def delete_message(
    conversation_id: int,
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> None:
    """Delete one question/answer inside a dialogue (web UI).

    Soft delete: the text stays archived in the DB (`messages.deleted_at`),
    and a tombstone records the removal so a later phone push (which always
    uploads the dialog's full local message list) cannot resurrect it.
    Other devices drop the message on their next sync pull.
    """
    conv = _fetch_conversation(db, conversation_id, account_id)
    msg = db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conv.id,
            Message.deleted_at.is_(None),
        )
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")

    deleted_by = device_label(request)
    msg.deleted_at = utcnow()
    msg.deleted_by = deleted_by
    db.add(
        MessageTombstone(
            conversation_id=conv.id, role=msg.role, content=msg.content,
            deleted_by=deleted_by,
        )
    )
    conv.updated_at = utcnow()
    db.commit()


@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=201)
def add_message(
    conversation_id: int,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> MessageOut:
    _fetch_conversation(db, conversation_id, account_id)
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
    account_id: str = Depends(get_account_id),
) -> dict:
    """🔥 Cache toggle: enable/disable prompt-cache warm-up for one dialog.

    When enabled, the background keeper pings this dialog's cached prefix
    (near-empty requests every 45 min) for the configured keep-alive window.
    The flag is persisted, so it survives server restarts.
    """
    conv = _fetch_conversation(db, conversation_id, account_id)
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


@router.get("/keepalive/pings")
def keepalive_pings(
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> dict:
    """Verification feed for the 🔥 Cache keep-alive: recent warm-up pings
    (dialog, model, when, ok/failed) WITHOUT creating any stub dialogs."""
    pings = [
        {
            "ts": p["ts"],
            "conversation_id": p["conversation_id"],
            "model": p["model"],
            "ok": p["ok"],
            "error": p["error"],
        }
        for p in cache_keeper.ping_log()
    ]
    # Account scope: only pings/titles for conversations this account owns.
    owned_ids = set(
        db.scalars(
            select(Conversation.id).where(Conversation.account_id == account_id)
        ).all()
    )
    pings = [p for p in pings if p["conversation_id"] in owned_ids]
    ids = {p["conversation_id"] for p in pings}
    titles: dict[int, str] = {}
    if ids:
        titles = {
            row[0]: row[1]
            for row in db.execute(
                select(Conversation.id, Conversation.title).where(
                    Conversation.id.in_(ids)
                )
            ).all()
        }
    for p in pings:
        p["title"] = titles.get(p["conversation_id"], f"dialog #{p['conversation_id']}")
    # Source of truth for "what is warming": the DB flag, restricted to live
    # (non-deleted) dialogs — stale tombstoned entries never show up here.
    enabled_rows = db.execute(
        select(Conversation.id, Conversation.title).where(
            Conversation.keepalive_enabled.is_(True),
            Conversation.deleted_at.is_(None),
            Conversation.account_id == account_id,
        )
    ).all()
    return {
        "interval_minutes": 45,
        "enabled": [
            {"conversation_id": row[0], "title": row[1]} for row in enabled_rows
        ],
        "pings": pings,
    }


def _fetch_conversation(
    db: Session, conversation_id: int, account_id: str
) -> Conversation:
    """Fetch a live dialog scoped to the calling account — a dialog owned by
    another account is indistinguishable from a missing one (404)."""
    conv = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
            Conversation.account_id == account_id,
        )
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv