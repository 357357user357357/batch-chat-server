"""Message operations shared by the web API and the sync (phone) API."""

from sqlalchemy.orm import Session

from app.models import Conversation, Message, MessageTombstone, utcnow


def edit_user_message(
    db: Session, conv: Conversation, msg: Message, new_content: str, device: str
) -> Message:
    """Edit one of the account's own questions (✏️) in place.

    Answers (assistant messages) are model output and are rejected by the
    callers. The PREVIOUS text is recorded as a tombstone — the same
    anti-resurrection mechanism deletions use — so a stale device push that
    still carries the old question can never re-append it. The dialog's
    updated_at is bumped, so every synced device receives the new text on its
    next pull.
    """
    old_content = msg.content
    msg.content = new_content
    db.add(
        MessageTombstone(
            conversation_id=conv.id, role=msg.role, content=old_content,
            deleted_by=device,
        )
    )
    conv.updated_at = utcnow()
    conv.modified_by = device
    db.flush()
    return msg
