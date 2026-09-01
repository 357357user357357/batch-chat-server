"""Multi-device dialog sync (Android app + any number of PCs).

Devices authenticate exactly like the web UI (POST /api/auth/login with the
shared password → bearer token) — that same token is the "sync key" that
links a device to this server. There is no separate device registry: any
client holding a valid token can push/pull, the same as the web UI can.

Sync is keyed by `external_id`, the id a dialog/batch was first created with
on whichever device made it. Conversations created purely through the web UI
have no external_id yet; `pull` assigns one (`srv-{id}`) the first time they
are returned so every dialog eventually has a stable cross-device id.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import AuthToken, Conversation, Message, utcnow
from app.schemas import (
    SyncConversationOut,
    SyncMessage,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from app.security import get_current_token
from app.services.phone_sync import batch_messages, dialog_messages, title_default

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/pull", response_model=SyncPullResponse)
def pull(
    since: str | None = None,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> SyncPullResponse:
    server_time = utcnow()

    query = select(Conversation).options(selectinload(Conversation.messages))
    if since:
        since_dt = _parse_since(since)
        query = query.where(Conversation.updated_at > since_dt)
    # Only "chat"/"batch" dialogs that a device could have created; kind is
    # kept as-is (both are just Conversation rows, see models.py).
    convs = db.scalars(query.order_by(Conversation.id)).all()

    out: list[SyncConversationOut] = []
    for conv in convs:
        if conv.external_id is None:
            conv.external_id = f"srv-{conv.id}"
    db.commit()

    for conv in convs:
        deleted = conv.deleted_at is not None
        out.append(
            SyncConversationOut(
                external_id=conv.external_id or f"srv-{conv.id}",
                kind=conv.kind,
                model=conv.model,
                title=conv.title,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                deleted=deleted,
                messages=[]
                if deleted
                else [
                    SyncMessage(role=m.role, content=m.content, model=m.model)
                    for m in conv.messages
                ],
            )
        )
    return SyncPullResponse(server_time=server_time, conversations=out)


@router.post("/push", response_model=SyncPushResponse)
def push(
    payload: SyncPushRequest,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> SyncPushResponse:
    created = 0
    updated = 0
    deleted = 0

    for dialog in payload.dialogs:
        if not dialog.id:
            continue
        messages = dialog_messages(dialog)
        if _upsert(db, external_id=dialog.id, kind="chat", model=dialog.model,
                    title=title_default(dialog.title, "Imported chat"), messages=messages):
            updated += 1
        else:
            created += 1

    for item in payload.batches:
        if not item.id:
            continue
        messages = batch_messages(item)
        if _upsert(db, external_id=item.id, kind="batch", model=item.model,
                    title=title_default(item.title, "Batch"), messages=messages):
            updated += 1
        else:
            created += 1

    if payload.deleted_external_ids:
        rows = db.scalars(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.external_id.in_(payload.deleted_external_ids))
        ).all()
        for conv in rows:
            if conv.deleted_at is not None:
                continue
            for msg in list(conv.messages):
                db.delete(msg)
            conv.deleted_at = utcnow()
            conv.updated_at = utcnow()
            deleted += 1

    db.commit()
    return SyncPushResponse(created=created, updated=updated, deleted=deleted, server_time=utcnow())


def _upsert(
    db: Session,
    *,
    external_id: str,
    kind: str,
    model: str | None,
    title: str,
    messages: list[tuple[str, str, str | None]],
) -> bool:
    """Create or update a conversation by external_id. Returns True if updated
    (False if newly created)."""
    conv = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.external_id == external_id)
    )
    if conv is None:
        conv = Conversation(external_id=external_id, kind=kind, model=model, title=title)
        db.add(conv)
        db.flush()
        for role, content, msg_model in messages:
            db.add(Message(conversation_id=conv.id, role=role, content=content, model=msg_model))
        return False

    conv.title = title
    conv.model = model or conv.model
    conv.deleted_at = None
    conv.updated_at = utcnow()
    for msg in list(conv.messages):
        db.delete(msg)
    db.flush()
    for role, content, msg_model in messages:
        db.add(Message(conversation_id=conv.id, role=role, content=content, model=msg_model))
    return True


def _parse_since(value: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return utcnow().replace(year=2000)
