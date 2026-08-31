from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuthToken, Conversation, Message
from app.schemas import (
    ImportRequest,
    ImportResponse,
    PhoneBatch,
    PhoneDialog,
    PhoneImportRequest,
)
from app.security import get_current_token

router = APIRouter(prefix="/api/import", tags=["import"])


def _title_default(item_title: str | None, fallback: str) -> str:
    return (item_title or fallback)[:255]


@router.post("", response_model=ImportResponse)
def import_conversations(
    payload: ImportRequest,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> ImportResponse:
    """Bulk-import conversations (generic format)."""
    conversations_created = 0
    messages_created = 0

    for item in payload.conversations:
        conv = Conversation(
            external_id=item.external_id,
            kind=item.kind,
            model=item.model,
            title=_title_default(item.title, "Imported chat"),
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
    _: AuthToken = Depends(get_current_token),
) -> ImportResponse:
    """Import a paste of Android app AsyncStorage data.

    Accepts {dialogs: [...], batches: [...]} with the exact shapes stored under
    ``openrouter.dialogs.v1`` and ``openrouter.batches.history.v1``.
    """
    conversations_created = 0
    messages_created = 0

    existing_ext = {
        (ext or "") for ext in db.scalars(select(Conversation.external_id)).all()
    }

    # 1. Regular chat dialogs
    for dialog in payload.dialogs or []:
        if dialog.id and dialog.id in existing_ext:
            continue
        messages = _dialog_messages(dialog)
        _store_conversation(
            db,
            external_id=dialog.id,
            kind="chat",
            model=dialog.model,
            title=_title_default(dialog.title, "Imported chat"),
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
        messages = _batch_messages(item)
        _store_conversation(
            db,
            external_id=item.id,
            kind="batch",
            model=item.model,
            title=_title_default(item.title, _batch_label(item.prompts)),
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


def _dialog_messages(dialog: PhoneDialog) -> list[tuple[str, str, str | None]]:
    return [(m.role, m.content, m.model or dialog.model) for m in dialog.messages]


def _batch_messages(item: PhoneBatch) -> list[tuple[str, str, str | None]]:
    """Flatten a batch item into (prompt, answer) message pairs.

    Results are matched to prompts by custom_id ``req-{n}`` (1-based, same as
    the app's CSV export). Failed jobs become assistant messages prefixed
    with "[error] ".
    """
    answers: dict[str, str] = {}
    raw_results = (item.batch or {}).get("results") or []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        custom_id = str(result.get("custom_id") or "")
        if result.get("error"):
            answers[custom_id] = f"[error] {result.get('error')}"
            continue
        if result.get("ok") is False:
            answers[custom_id] = f"[error] HTTP {result.get('status', '?')}"
            continue
        body = result.get("response") or {}
        content = body.get("body", {}) if isinstance(body, dict) else {}
        answer = ""
        if isinstance(content, dict):
            choices = content.get("choices") or []
            if choices:
                answer = str(
                    choices[0].get("message", {}).get("content", "") or ""
                )
        answers[custom_id] = answer

    messages: list[tuple[str, str, str | None]] = []
    for index, prompt in enumerate(item.prompts, start=1):
        if not prompt or not prompt.strip():
            continue
        messages.append(("user", prompt, None))
        answer = answers.get(f"req-{index}", "")
        if answer:
            messages.append(("assistant", answer, item.model))
    return messages


def _batch_label(prompts: list[str]) -> str:
    first = next((p for p in prompts if p and p.strip()), "") or "Batch chat"
    cleaned = " ".join(first.split())
    return cleaned[:42] + ("…" if len(cleaned) > 42 else "")