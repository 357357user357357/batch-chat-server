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
    RetryRequest,
    RetryResponse,
)
from app.security import get_account_id
from app.services import cache_keeper, tavily
from app.services.providers import (
    ProviderError,
    chat_completion_full,
    default_models,
)
from app.services import openrouter
from app.models import live_message_sort_key, next_sort_index

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


def build_answer_system(
    user_system: str | None, question: str, web_search: bool
) -> tuple[str, bool]:
    """System prompt for answering one question (shared by send and retry):
    optional Tavily web-search injection, then the server-clock datetime
    prompt, then the clock preamble. Returns (system_text, web_search_used).
    """
    system = user_system or ""
    web_search_used = False
    if web_search and tavily.is_configured():
        try:
            results = tavily.search_web(question, max_results=3)
            if results:
                web_search_used = True
                suffix = (
                    "Use the most relevant web context below when answering.\n\n"
                    + tavily.web_search_context(question, results)
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
    return system_prompt_with_current_time(system), web_search_used


def call_model(
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> ChatResponseItem:
    """One model call that never raises — failures become ok=False items so a
    single broken model can't crash the whole batch."""
    try:
        info = chat_completion_full(
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
        return ChatResponseItem(
            model=model, ok=True,
            content=info.get("content"),
            reasoning=reasoning_effort,
            provider=info.get("provider"),
            gen_id=info.get("gen_id"),
            tokens_prompt=info.get("tokens_prompt"),
            tokens_completion=info.get("tokens_completion"),
            total_tokens=info.get("total_tokens"),
            cost=info.get("cost"),
        )
    except ProviderError as exc:
        return ChatResponseItem(model=model, ok=False, error=str(exc))
    except Exception as exc:  # defensive: never crash the whole batch
        return ChatResponseItem(model=model, ok=False, error=f"Unexpected error: {exc}")


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
        sort_index=next_sort_index(db, conv.id),
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
            .order_by(
                Message.sort_index.is_(None), Message.sort_index, Message.id
            )
        )
    ]

    # Optional Tavily web search, injected into the system prompt like the app.
    system, web_search_used = build_answer_system(
        payload.system, payload.user_message, payload.web_search
    )

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for role, content, _model in history:
        messages.append({"role": role, "content": content})

    # 4. Call every model in parallel with a thread pool
    limit_models = payload.models[:20]
    responses: dict[str, ChatResponseItem] = {}

    assistant_ids: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=min(len(limit_models), 8)) as pool:
        future_map = {pool.submit(call_model, model, messages,
                                  temperature=payload.temperature,
                                  max_tokens=payload.max_tokens,
                                  reasoning_effort=payload.reasoning_effort): model
                      for model in limit_models}
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
                    reasoning=item.reasoning,
                    provider=item.provider,
                    gen_id=item.gen_id,
                    tokens_prompt=item.tokens_prompt,
                    tokens_completion=item.tokens_completion,
                    total_tokens=item.total_tokens,
                    cost=item.cost,
                    sort_index=next_sort_index(db, conv.id),
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


@router.post("/retry", response_model=RetryResponse)
def retry_answer(
    payload: RetryRequest,
    request: Request,
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> RetryResponse:
    """🔄 Re-answer one assistant reply with other model(s).

    The question that precedes the answer is re-asked to every model in
    `models` using the context UP TO that question (the retried answer and
    everything after it are excluded — the same view the original model had).
    New answers are stored immediately after the retried one, so they show up
    next to it on the web and on every synced device; older answers are kept
    for comparison (delete any you don't want as usual).
    """
    if payload.conversation_id is None and not payload.external_id:
        raise HTTPException(
            status_code=422, detail="conversation_id or external_id is required"
        )

    # Resolve the dialog (DB id for the web, sync external_id for the phone).
    if payload.conversation_id is not None:
        conv = db.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.deleted_at.is_(None),
                Conversation.account_id == account_id,
            )
        )
    else:
        conv = db.scalar(
            select(Conversation).where(
                Conversation.external_id == payload.external_id,
                Conversation.deleted_at.is_(None),
                Conversation.account_id == account_id,
            )
        )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg = db.scalar(
        select(Message).where(
            Message.id == payload.message_id,
            Message.conversation_id == conv.id,
            Message.deleted_at.is_(None),
        )
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.role != "assistant":
        raise HTTPException(
            status_code=400, detail="Retry works on assistant answers only"
        )

    live = sorted(
        (m for m in conv.messages if m.deleted_at is None),
        key=live_message_sort_key,
    )
    try:
        idx = next(i for i, m in enumerate(live) if m.id == msg.id)
    except StopIteration:  # defensive: query above already guarantees it
        raise HTTPException(status_code=404, detail="Message not found")

    # The question this answer belongs to = the closest preceding user message.
    q_idx = next(
        (i for i in range(idx - 1, -1, -1) if live[i].role == "user"), None
    )
    if q_idx is None:
        raise HTTPException(
            status_code=400, detail="No question found before this answer"
        )
    question = live[q_idx].content

    # Positioning: insert the new answers between the retried one and whatever
    # came after it. sort_index values are backfilled from id for old rows, so
    # there is always a usable gap; renumber defensively when it got too tight.
    nxt = live[idx + 1] if idx + 1 < len(live) else None
    orig_si = msg.sort_index
    next_si = nxt.sort_index if nxt is not None else None
    if orig_si is None or (next_si is not None and next_si - orig_si < 1e-9):
        for rank, m in enumerate(live):
            m.sort_index = float(rank + 1)
        db.flush()
        orig_si = msg.sort_index
        next_si = nxt.sort_index if nxt is not None else None

    # Context exactly as the original model saw it: everything up to and
    # including the question, nothing after it.
    system, _web_used = build_answer_system(
        payload.system, question, payload.web_search
    )
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for m in live[: q_idx + 1]:
        messages.append({"role": m.role, "content": m.content})

    models = list(dict.fromkeys(payload.models))[:20]
    responses: dict[str, ChatResponseItem] = {}
    assistant_ids: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=min(len(models), 8)) as pool:
        future_map = {pool.submit(call_model, model, messages,
                                  temperature=payload.temperature,
                                  max_tokens=payload.max_tokens,
                                  reasoning_effort=payload.reasoning_effort): model
                      for model in models}
        for future in as_completed(future_map):
            item = future.result()
            responses[item.model] = item

            if item.ok and item.content:
                # Slot between the retried answer and the next message; each
                # fresh answer gets its own slice of the gap.
                if next_si is not None:
                    step = (next_si - orig_si) / (len(models) + 1)
                    slot = orig_si + step * (models.index(item.model) + 1)
                else:
                    slot = orig_si + models.index(item.model) + 1
                assistant_msg = Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=item.content,
                    model=item.model,
                    reasoning=item.reasoning,
                    provider=item.provider,
                    gen_id=item.gen_id,
                    tokens_prompt=item.tokens_prompt,
                    tokens_completion=item.tokens_completion,
                    total_tokens=item.total_tokens,
                    cost=item.cost,
                    sort_index=slot,
                )
                db.add(assistant_msg)
                db.flush()
                assistant_ids[item.model] = assistant_msg.id
    conv.updated_at = utcnow()
    db.commit()

    ordered = [responses[m] for m in models if m in responses] or []
    for item in ordered:
        item.message_id = assistant_ids.get(item.model)
    return RetryResponse(
        conversation_id=conv.id,
        external_id=conv.external_id,
        source_message_id=msg.id,
        responses=ordered,
    )