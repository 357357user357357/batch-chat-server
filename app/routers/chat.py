from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timezone
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.device import device_label
from app.models import Conversation, Message, utcnow
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ChatResponseItem,
    MessageOut,
)
from app.security import get_account_id
from app.services import cache_keeper, tavily
from app.services.providers import ProviderError, chat_completion, default_models
from app.services import openrouter

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
    # Pricing (USD per token) for the picker models, PER CHAT MODE, from the
    # public OpenRouter catalog. Live = catalog price. Flex has no separate
    # catalog entry — OpenAI's documented flex discount is 50%, the same
    # factor OpenRouter's ":batch" ids carry (verified in the catalog).
    catalog = openrouter.fetch_model_pricing()
    pricing: dict[str, dict[str, dict[str, float]]] = {}
    for model in default_models():
        base, tier = openrouter.split_model_variant(model)
        # split_model_variant keeps ":batch" in the base (it's part of the
        # OpenRouter id) — the catalog is keyed by the plain id.
        lookup = base.removesuffix(openrouter.BATCH_SUFFIX).lstrip("~")
        entry = catalog.get(base.removesuffix(openrouter.BATCH_SUFFIX)) or catalog.get(lookup)
        if not entry:
            continue
        half = {key: value * 0.5 for key, value in entry.items()}
        pricing[model] = {"live": dict(entry), "flex": dict(half), "batch": dict(half)}
    return {
        "default_models": default_models(),
        "default_live_model": openrouter.DEFAULT_LIVE_MODEL,
        "default_batch_model": openrouter.DEFAULT_BATCH_MODEL,
        "pricing": pricing,
    }


@router.post("/send", response_model=ChatResponse)
def send_chat(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> ChatResponse:
    # 1. Find or create the conversation (scoped to the calling account)
    if payload.conversation_id is not None:
        conv = db.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.deleted_at.is_(None),
                Conversation.account_id == account_id,
            )
        )
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        title = (payload.user_message[:50] + "…") if len(payload.user_message) > 50 else payload.user_message
        device = device_label(request)
        conv = Conversation(title=title[:255] or "New chat", kind="chat",
                            account_id=account_id,
                            origin_device=device, modified_by=device)
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
    web_search_used = False
    if payload.web_search and tavily.is_configured():
        try:
            results = tavily.search_web(payload.user_message, max_results=3)
            if results:
                web_search_used = True
                suffix = (
                    "Use the most relevant web context below when answering.\n\n"
                    + tavily.web_search_context(payload.user_message, results)
                )
                system = (system + "\n\n" + suffix).strip()
        except ProviderError:
            # A failed search shouldn't block the chat — answer without context.
            pass

    # Current date/time from the reliable server clock (mirrors the Android
    # app's currentDateTimePrompt): "what's the time now" answers from the
    # clock, not from stale web results.
    now = utcnow().replace(tzinfo=timezone.utc).astimezone()
    datetime_prompt = (
        f"Current date and time: {now.strftime('%A, %d %B %Y, %H:%M')} "
        f"({now.tzname() or 'UTC'}) — the reliable server clock. "
        "Answer questions about the current time, date, day of the week, or "
        "time zones using this information. Do not use web search results "
        "for the current time."
    )
    system = (datetime_prompt + "\n\n" + system).strip()

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
                reasoning_effort=payload.reasoning_effort,
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
    # Record the exact prefix of this request so warming can be enabled for
    # this dialog later via the 🔥 Cache toggle (no automatic pings).
    ok_models = [i.model for i in ordered if i.ok]
    if ok_models:
        try:
            cache_keeper.record(conv.id, system, ok_models)
        except Exception:  # keep-alive must never break the chat
            pass
    return ChatResponse(
        conversation_id=conv.id,
        conversation_title=conv.title,
        user_message=MessageOut.model_validate(user_msg),
        responses=ordered,
        web_search_used=web_search_used,
    )