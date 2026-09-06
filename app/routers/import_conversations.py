from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Conversation, Message
from app.schemas import (
    ImportRequest,
    ImportResponse,
    PhoneImportRequest,
)
from app.security import get_account_id
from app.services.phone_sync import batch_label, batch_messages, dialog_messages, title_default

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("", response_model=ImportResponse)
def import_conversations(
    payload: ImportRequest,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> ImportResponse:
    """Bulk-import conversations (generic format)."""
    conversations_created = 0
    messages_created = 0

    for item in payload.conversations:
        conv = Conversation(
            external_id=item.external_id,
            kind=item.kind,
            model=item.model,
            title=title_default(item.title, "Imported chat"),
            account_id=account_id,
        )
        db.add(conv)
        db.flush()
        for msg in item.messages:
            db.add(
                Message(
                    conversation_id=conv.id,
                    role=msg.role,
                    content=msg.content,
                    model=msg.model,
                )
            )
            messages_created += 1
        conversations_created += 1

    db.commit()
    return ImportResponse(
        conversations_created=conversations_created,
        messages_created=messages_created,
    )


@router.post("/phone", response_model=ImportResponse)
def import_phone_data(
    payload: PhoneImportRequest,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> ImportResponse:
    """Import a paste of Android app AsyncStorage data.

    Accepts {dialogs: [...], batches: [...]} with the exact shapes stored under
    ``openrouter.dialogs.v1`` and ``openrouter.batches.history.v1``.
    """
    conversations_created = 0
    messages_created = 0

    existing_ext = {
        (ext or "")
        for ext in db.scalars(
            select(Conversation.external_id).where(
                Conversation.account_id == account_id
            )
        ).all()
    }

    # 1. Regular chat dialogs
    for dialog in payload.dialogs or []:
        if dialog.id and dialog.id in existing_ext:
            continue
        messages = dialog_messages(dialog)
        _store_conversation(
            db,
            account_id=account_id,
            external_id=dialog.id,
            kind="chat",
            model=dialog.model,
            title=title_default(dialog.title, "Imported chat"),
            messages=messages,
        )
        if dialog.id and messages:
            existing_ext.add(dialog.id)
        conversations_created += 1
        messages_created += len(messages)

    # 2. Batch runs (prompts + answers from the async Batch API results)
    for item in payload.batches or []:
        if item.id and item.id in existing_ext:
            continue
        messages = batch_messages(item)
        _store_conversation(
            db,
            account_id=account_id,
            external_id=item.id,
            kind="batch",
            model=item.model,
            title=title_default(item.title, batch_label(item.prompts)),
            messages=messages,
        )
        if item.id and messages:
            existing_ext.add(item.id)
        conversations_created += 1
        messages_created += len(messages)

    db.commit()
    return ImportResponse(
        conversations_created=conversations_created,
        messages_created=messages_created,
    )


def _store_conversation(
    db: Session,
    *,
    account_id: str,
    external_id: str | None,
    kind: str,
    model: str | None,
    title: str,
    messages: list[tuple[str, str, str | None]],
) -> None:
    conv = Conversation(
        external_id=external_id,
        kind=kind,
        model=model,
        title=title,
        account_id=account_id,
    )
    db.add(conv)
    db.flush()
    for role, content, msg_model in messages:
        db.add(
            Message(
                conversation_id=conv.id,
                role=role,
                content=content,
                model=msg_model,
            )
        )