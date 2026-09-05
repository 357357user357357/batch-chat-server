"""Multi-device dialog sync (Android app + any number of PCs).

Devices authenticate exactly like the web UI (POST /api/auth/login with the
shared password → bearer token) — that same token is the "sync key" that
links a device to this server. There is no separate device registry: any
client holding a valid token can push/pull, the same as the web UI can.

Sync is keyed by `external_id`, the id a dialog/batch was first created with
on whichever device made it. Conversations created purely through the web UI
have no external_id yet; `pull` assigns one (`srv-{id}`) the first time they
are returned so every dialog eventually has a stable cross-device id.

Every record the master server stores carries an audit trail: WHO created it
(origin_device), WHO last modified it (modified_by) and WHO deleted it
(deleted_by) — plus the deletion date. Soft-deleted records (tombstones) are
never removed from the master DB; they are the archive.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.device import device_label
from app.models import AuthToken, Conversation, Message, MessageTombstone, utcnow
from app.schemas import (
    SyncConversationOut,
    SyncMessage,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from app.security import get_current_token
from app.services.phone_sync import batch_messages, dialog_messages, title_default
from app.services.settings_store import adopt_missing_keys, syncable_keys

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/pull", response_model=SyncPullResponse)
def pull(
    since: str | None = None,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> SyncPullResponse:
    server_time = utcnow()

    from sqlalchemy import or_

    query = select(Conversation).options(selectinload(Conversation.messages))
    if since:
        since_dt = _parse_since(since)
        # Tombstoned (deleted) dialogs are ALWAYS included, regardless of the
        # `since` cursor: a device that synced between a deletion and now (or
        # missed the tombstone for any reason) still learns about the deletion
        # and drops its stale local copy on the next pull. They carry no
        # messages, so the extra payload is tiny.
        query = query.where(
            or_(
                Conversation.updated_at > since_dt,
                Conversation.deleted_at.is_not(None),
            )
        )
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
                # Audit trail: whose record this is / was, and what happened.
                origin_device=conv.origin_device,
                modified_by=conv.modified_by,
                deleted_at=conv.deleted_at if deleted else None,
                deleted_by=conv.deleted_by if deleted else None,
                messages=[]
                if deleted
                else [
                    SyncMessage(role=m.role, content=m.content, model=m.model)
                    for m in conv.messages
                    if m.deleted_at is None
                ],
            )
        )
    return SyncPullResponse(
        server_time=server_time,
        conversations=out,
        keys=syncable_keys(),
    )


@router.post("/push", response_model=SyncPushResponse)
def push(
    payload: SyncPushRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> SyncPushResponse:
    created = 0
    updated = 0
    deleted = 0
    skipped_deleted = 0
    device = device_label(request)

    for dialog in payload.dialogs:
        if not dialog.id:
            continue
        messages = dialog_messages(dialog)
        status = _upsert(db, external_id=dialog.id, kind="chat", model=dialog.model,
                         title=title_default(dialog.title, "Imported chat"), messages=messages,
                         device=device)
        if status == "updated":
            updated += 1
        elif status == "skipped_deleted":
            skipped_deleted += 1  # tombstoned on the master — stale local copy
        else:
            created += 1

    for item in payload.batches:
        if not item.id:
            continue
        messages = batch_messages(item)
        status = _upsert(db, external_id=item.id, kind="batch", model=item.model,
                         title=title_default(item.title, "Batch"), messages=messages,
                         device=device)
        if status == "updated":
            updated += 1
        elif status == "skipped_deleted":
            skipped_deleted += 1
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
            # Keep the messages as an archive in the DB; only the tombstone
            # (deleted_at) marks it deleted. Other devices still see
            # `deleted: true` on pull and drop their local copy.
            conv.deleted_at = utcnow()
            conv.updated_at = utcnow()
            conv.deleted_by = device
            deleted += 1

    # Keys a device offered fill gaps on the server (server-first: an existing
    # server key is never overwritten).
    adopt_missing_keys(db, payload.keys)

    db.commit()
    return SyncPushResponse(created=created, updated=updated, deleted=deleted,
                            skipped_deleted=skipped_deleted, server_time=utcnow())


def _upsert(
    db: Session,
    *,
    external_id: str,
    kind: str,
    model: str | None,
    title: str,
    messages: list[tuple[str, str, str | None]],
    device: str = "unknown",
) -> str:
    """Create or update a conversation by external_id. Returns "created",
    "updated" or "skipped_deleted" (a stale push against a tombstoned dialog).

    Individually deleted Q/A are preserved: messages already archived on the
    server (deleted_at set) are never wiped by a device push, and incoming
    messages matching a tombstone (role + exact content) are skipped so a
    deleted question/answer cannot be resurrected by an older device copy.

    Audit trail: a new record gets origin_device (whose record it is), and
    every device-driven change marks modified_by (last modifier; the date is
    updated_at). Tombstoned (deleted) dialogs are never resurrected by a push.
    """
    conv = db.scalar(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.external_id == external_id)
    )
    is_new = conv is None
    if is_new:
        conv = Conversation(external_id=external_id, kind=kind, model=model, title=title,
                            origin_device=device, modified_by=device)
        db.add(conv)
        db.flush()
        tombstones = set()
    elif conv.deleted_at is not None:
        # This dialog was deleted (on the web or another device) and the master
        # server keeps the tombstone as the archive of truth. A push of a stale
        # local copy must NOT resurrect it — the pushing device will drop its
        # local copy when it pulls and sees `deleted: true`.
        return "skipped_deleted"
    else:
        conv.title = title
        conv.model = model or conv.model
        conv.updated_at = utcnow()
        conv.modified_by = device
        tombstones = set(
            db.execute(
                select(MessageTombstone.role, MessageTombstone.content).where(
                    MessageTombstone.conversation_id == conv.id
                )
            ).all()
        )
        # Drop only the live messages; archived (deleted) ones stay in the DB.
        for msg in list(conv.messages):
            if msg.deleted_at is None:
                db.delete(msg)
        db.flush()

    for role, content, msg_model in messages:
        if (role, content) in tombstones:
            continue  # deleted from the web — keep it deleted
        db.add(Message(conversation_id=conv.id, role=role, content=content, model=msg_model))
    return "updated" if not is_new else "created"


def _parse_since(value: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return utcnow().replace(year=2000)
