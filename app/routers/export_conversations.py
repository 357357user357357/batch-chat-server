"""Export the account's dialogs in the Android app's AsyncStorage format so
they can be imported on the phone (the mirror of /api/import/phone).

The output uses the exact canonical keys the app stores —
``openrouter.dialogs.v1`` (list of PhoneDialog) and
``openrouter.batches.history.v1`` (list of PhoneBatch) — so the JSON can be
pasted/imported on Android directly, and feeding it back through the import
endpoint (or the sync push) reproduces the same dialogs.

Dialog ids reuse the sync external_id (`srv-{id}` for web-born dialogs, the
same id /api/sync/pull assigns), so a phone that imports the file and then
syncs merges with the server instead of duplicating everything.
"""

from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Conversation, Message
from app.security import get_account_id

router = APIRouter(prefix="/api/export", tags=["export"])

# OpenRouter metadata carried through the phone format (ImportMessage fields).
_MESSAGE_META_FIELDS = (
    "reasoning", "provider", "gen_id",
    "tokens_prompt", "tokens_cached", "tokens_completion",
    "total_tokens", "cost",
)


@router.get("/phone")
def export_phone_data(
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> dict:
    convs = db.scalars(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.account_id == account_id,
            Conversation.deleted_at.is_(None),
        )
        .order_by(Conversation.id)
    ).all()

    dialogs: list[dict] = []
    batches: list[dict] = []
    for conv in convs:
        messages = [m for m in conv.messages if m.deleted_at is None]
        messages.sort(key=lambda m: (m.sort_index is None, m.sort_index, m.id))
        if conv.kind == "batch":
            batches.append(_batch_item(conv, messages))
        else:
            dialogs.append(_dialog_item(conv, messages))

    return {
        "openrouter.dialogs.v1": dialogs,
        "openrouter.batches.history.v1": batches,
    }


def _dialog_item(conv: Conversation, messages: list[Message]) -> dict:
    return {
        "id": conv.external_id or f"srv-{conv.id}",
        "title": conv.title,
        "model": conv.model,
        "createdAt": _ms(conv.created_at),
        "updatedAt": _ms(conv.updated_at),
        "messages": [_message_dict(m, fallback_model=conv.model) for m in messages],
    }


def _batch_item(conv: Conversation, messages: list[Message]) -> dict:
    """A batch as PhoneBatch: prompts + a reconstructed `batch.results` list
    keyed by custom_id `req-{n}` (the shape batch_messages() reads back)."""
    prompts: list[str] = []
    results: list[dict] = []
    req = 0
    answer: Message | None = None
    for msg in messages:
        if msg.role == "user":
            prompts.append(msg.content)
            req += 1
            answer = None
        elif msg.role == "assistant" and req > 0 and answer is None:
            # First answer for the current prompt wins (extra retried answers
            # stay server-only — the phone format stores one result per req).
            answer = msg
            content = msg.content or ""
            if content.startswith("[error] "):
                results.append({"custom_id": f"req-{req}", "error": content[8:]})
            else:
                results.append(
                    {
                        "custom_id": f"req-{req}",
                        "response": {
                            "status_code": 200,
                            "body": {
                                "choices": [
                                    {"message": {"content": content}}
                                ]
                            },
                        },
                    }
                )
    return {
        "id": conv.external_id or f"srv-{conv.id}",
        "title": conv.title,
        "model": conv.model,
        "prompts": prompts,
        "createdAt": _ms(conv.created_at),
        "updatedAt": _ms(conv.updated_at),
        "batch": {"id": conv.external_id, "status": "completed", "results": results},
    }


def _message_dict(msg: Message, *, fallback_model: str | None) -> dict:
    out = {"role": msg.role, "content": msg.content}
    model = msg.model if msg.role == "assistant" else (msg.model or fallback_model)
    if model:
        out["model"] = model
    for field in _MESSAGE_META_FIELDS:
        value = getattr(msg, field, None)
        if value is not None:
            out[field] = value
    return out


def _ms(dt) -> int | None:
    """Naive-UTC datetime → ms epoch int (the app's storage shape)."""
    if dt is None:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
