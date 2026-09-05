from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

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
from app.services import tavily
from app.services.providers import ProviderError, chat_completion, default_models

router = APIRouter(prefix="/api/chat", tags=["chat"])


def system_prompt_with_current_time(user_system: str | None) -> str:
    """Prepend the server's current date/time to the system prompt.

    Models have no clock, so without this a question like "what time is it
    now" gets answered from training data or — worse — from whatever time a
    fetched web page happens to mention. The server clock is authoritative.
    """
    now = datetime.now(timezone.utc)
    base = (
        f"Current date and time: {now.strftime('%A, %d %B %Y, %H:%M UTC')} "
        "(the reliable server clock). Answer questions about the current "
        "time, date, or day of the week from this — never from web snippets "
        "or training data."
    )
    text = (user_system or "").strip()
    return f"{base}\n\n{text}".strip()


@router.get("/models")
def available_models() -> dict:
    return {"default_models": default_models()}


@router.post("/send", response_model=ChatResponse)
def send_chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    _: AuthToken = Depends(get_current_token),
) -> ChatResponse:
    # 1. Find or create the conversation
    if payload.conversation_id is not None:
        conv = db.get(Conversation, payload.conversation_id)
        if conv is None or conv.deleted_at is not None:
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
            select(Message)
            .where(
                Message.conversation_id == conv.id,
                Message.deleted_at.is_(None),  # skip individually deleted Q/A
            )
            .order_by(Message.id)
        )
    ]

    # Optional Tavily web search, injected into the system prompt like the app.
    system = payload.system or ""
    if payload.web_search and tavily.is_configured():
        try:
            results = tavily.search_web(payload.user_message, max_results=3)
            if results:
                suffix = (
                    "Use the most relevant web context below when answering.\n\n"
                    + tavily.web_search_context(payload.user_message, results)
                )
                system = (system + "\n\n" + suffix).strip()
        except ProviderError:
            # A failed search shouldn't block the chat — answer without context.
            pass

    # The model has no clock of its own: give it the server's real date/time
    # so questions like "what time is it now" are answered correctly and stale
    # web snippets can't pose as the present moment.
    system = system_prompt_with_current_time(system)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
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
        except ProviderError as exc:
            return ChatResponseItem(model=model, ok=False, error=str(exc))
        except Exception as exc:  # defensive: never crash the whole batch
            return ChatResponseItem(model=model, ok=False, error=f"Unexpected error: {exc}")

    assistant_ids: dict[str, int] = {}

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
                db.flush()
                assistant_ids[item.model] = assistant_msg.id
    conv.updated_at = utcnow()
    db.commit()

    ordered = [responses[m] for m in payload.models if m in responses] or []
    for item in ordered:
        # Expose the DB id so the web UI can delete a fresh answer right away
        item.message_id = assistant_ids.get(item.model)
    return ChatResponse(
        conversation_id=conv.id,
        conversation_title=conv.title,
        user_message=MessageOut.model_validate(user_msg),
        responses=ordered,
    )