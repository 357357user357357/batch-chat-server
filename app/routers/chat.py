from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuthToken, Conversation, Message, utcnow
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ChatResponseItem,
    MessageOut,
)
from app.security import get_current_token
from app.services.openrouter import DEFAULT_MODELS, OpenRouterError, chat_completion

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/models")
def available_models() -> dict:
    return {"default_models": DEFAULT_MODELS}


@router.post("/send", response_model=ChatResponse)
def send_chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> ChatResponse:
    # 1. Find or create the conversation
    if payload.conversation_id is not None:
        conv = db.get(Conversation, payload.conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        title = (payload.user_message[:50] + "…") if len(payload.user_message) > 50 else payload.user_message
        conv = Conversation(title=title[:255] or "New chat", kind="chat")
        db.add(conv)
        db.commit()
        db.refresh(conv)

    # 2. Store the user message
    user_msg = Message(
        conversation_id=conv.id,
        role="user",
        content=payload.user_message,
    )
    db.add(user_msg)
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(user_msg)

    # 3. Build the full context from conversation history
    history: list[tuple[str, str, str | None]] = [
        (m.role, m.content, m.model)
        for m in db.scalars(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)
        )
    ]

    messages: list[dict[str, str]] = []
    if payload.system:
        messages.append({"role": "system", "content": payload.system})
    for role, content, _model in history:
        messages.append({"role": role, "content": content})

    # 4. Call every model in parallel with a thread pool
    limit_models = payload.models[:20]
    responses: dict[str, ChatResponseItem] = {}

    def _call(model: str) -> ChatResponseItem:
        try:
            content = chat_completion(
                model,
                messages,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            )
            return ChatResponseItem(model=model, ok=True, content=content)
        except OpenRouterError as exc:
            return ChatResponseItem(model=model, ok=False, error=str(exc))
        except Exception as exc:  # defensive: never crash the whole batch
            return ChatResponseItem(model=model, ok=False, error=f"Unexpected error: {exc}")

    with ThreadPoolExecutor(max_workers=min(len(limit_models), 8)) as pool:
        future_map = {pool.submit(_call, model): model for model in limit_models}
        for future in as_completed(future_map):
            item = future.result()
            responses[item.model] = item

            # 5. Persist successful assistant replies
            if item.ok and item.content:
                assistant_msg = Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=item.content,
                    model=item.model,
                )
                db.add(assistant_msg)
    conv.updated_at = utcnow()
    db.commit()

    ordered = [responses[m] for m in payload.models if m in responses] or []
    return ChatResponse(
        conversation_id=conv.id,
        conversation_title=conv.title,
        user_message=MessageOut.model_validate(user_msg),
        responses=ordered,
    )