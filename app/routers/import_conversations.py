from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuthToken, Conversation, Message
from app.schemas import ImportRequest, ImportResponse
from app.security import get_current_token

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("", response_model=ImportResponse)
def import_conversations(
    payload: ImportRequest,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> ImportResponse:
    """Bulk-import conversations (e.g. exported from the Android app)."""
    conversations_created = 0
    messages_created = 0

    for item in payload.conversations:
        conv = Conversation(title=(item.title or "Imported chat")[:255])
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