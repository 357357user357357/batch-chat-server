"""Usage statistics for the web UI.

Prompt-cache monitoring (OpenRouter-dashboard style): daily prompt tokens
split into cached (served from OpenRouter's prompt cache at the discounted
rate) and uncached. The owner sees the whole instance — it manages the one
shared provider key; client accounts only see their own messages.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BatchJob, Conversation, Message, utcnow
from app.security import get_account_id
from app.services.account import default_account_id

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/prompt-cache")
def prompt_cache(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> dict:
    """Daily cached/uncached prompt-token buckets for the last `days` days."""
    is_owner = account_id == default_account_id(db)

    start = (utcnow() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    stmt = (
        select(
            Message.created_at,
            Message.tokens_prompt,
            Message.tokens_cached,
            Message.cost,
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Message.role == "assistant",
            Message.deleted_at.is_(None),
            Message.created_at >= start,
        )
    )
    if not is_owner:
        stmt = stmt.where(Conversation.account_id == account_id)
    rows = db.execute(stmt).all()

    # Daily buckets keyed by UTC date; missing days are filled below so the
    # chart never shows gaps (a zero-height bar, like OpenRouter's own).
    buckets: dict[str, dict] = {}
    for created_at, prompt, cached, cost in rows:
        if created_at is None:
            continue
        day = created_at.date().isoformat()
        entry = buckets.setdefault(
            day, {"date": day, "cached": 0, "uncached": 0, "prompt": 0, "cost": 0.0}
        )
        if prompt:
            # OpenRouter reports cached tokens inside prompt_tokens_details,
            # i.e. cached ⊆ prompt. Old rows (NULL cached) count as uncached.
            cached_part = max(0, cached or 0)
            entry["cached"] += cached_part
            entry["uncached"] += max(0, prompt - cached_part)
            entry["prompt"] += prompt
        if cost:
            entry["cost"] += cost

    ordered = []
    day = start.date()
    today = utcnow().date()
    while day <= today:
        ordered.append(buckets.get(
            day.isoformat(),
            {"date": day.isoformat(), "cached": 0, "uncached": 0, "prompt": 0, "cost": 0.0},
        ))
        day += timedelta(days=1)

    total_prompt = sum(b["prompt"] for b in ordered)
    total_cached = sum(b["cached"] for b in ordered)
    return {
        "days": days,
        "buckets": ordered,
        "totals": {
            "prompt": total_prompt,
            "cached": total_cached,
            "uncached": total_prompt - total_cached,
            "cost": round(sum(b["cost"] for b in ordered), 6),
            "cached_share": round(total_cached / total_prompt * 100, 1) if total_prompt else 0.0,
        },
    }


@router.get("/overview")
def overview(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    account_id: str = Depends(get_account_id),
) -> dict:
    """Usage-dashboard payload: daily cost/token buckets, per-model split,
    top conversations and recent batch jobs for the last `days` days.

    Batch answers are messages inside kind="batch" conversations (sync
    carries per-answer tokens/cost), so one message-level aggregation covers
    live chat and batches alike. Same owner-vs-client scoping as prompt-cache.
    """
    is_owner = account_id == default_account_id(db)

    start = (utcnow() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    stmt = (
        select(
            Message.created_at,
            Message.model,
            Message.tokens_prompt,
            Message.tokens_completion,
            Message.total_tokens,
            Message.cost,
            Conversation.id,
            Conversation.title,
            Conversation.kind,
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Message.role == "assistant",
            Message.deleted_at.is_(None),
            Conversation.deleted_at.is_(None),
            Message.created_at >= start,
        )
    )
    if not is_owner:
        stmt = stmt.where(Conversation.account_id == account_id)
    rows = db.execute(stmt).all()

    daily: dict[str, dict] = {}
    models: dict[str, dict] = {}
    convs: dict[int, dict] = {}
    for created_at, model, prompt, completion, total, cost, cid, title, kind in rows:
        if created_at is None:
            continue
        day = created_at.date().isoformat()
        d = daily.setdefault(
            day,
            {"date": day, "cost": 0.0, "prompt": 0, "completion": 0,
             "total": 0, "messages": 0},
        )
        d["cost"] += cost or 0.0
        d["prompt"] += prompt or 0
        d["completion"] += completion or 0
        d["total"] += total or 0
        d["messages"] += 1

        mkey = model or "(unknown)"
        m = models.setdefault(
            mkey,
            {"model": mkey, "cost": 0.0, "prompt": 0, "completion": 0,
             "total": 0, "messages": 0},
        )
        m["cost"] += cost or 0.0
        m["prompt"] += prompt or 0
        m["completion"] += completion or 0
        m["total"] += total or 0
        m["messages"] += 1

        c = convs.setdefault(
            cid,
            {"id": cid, "title": title, "kind": kind, "cost": 0.0,
             "total": 0, "messages": 0},
        )
        c["cost"] += cost or 0.0
        c["total"] += total or 0
        c["messages"] += 1

    # Zero-filled daily series so charts never show gaps.
    ordered_daily = []
    day = start.date()
    today = utcnow().date()
    while day <= today:
        ordered_daily.append(daily.get(
            day.isoformat(),
            {"date": day.isoformat(), "cost": 0.0, "prompt": 0,
             "completion": 0, "total": 0, "messages": 0},
        ))
        day += timedelta(days=1)

    batch_rows = db.execute(
        select(BatchJob)
        .order_by(BatchJob.created_at.desc())
        .limit(15)
    ).scalars().all()
    batches = [
        {
            "id": b.id,
            "title": b.title,
            "model": b.model,
            "status": b.status,
            "total_items": b.total_items,
            "completed_items": b.completed_items,
            "failed_items": b.failed_items,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in batch_rows
    ]

    top_convs = sorted(convs.values(), key=lambda c: -c["cost"])[:10]
    total_cost = sum(d["cost"] for d in ordered_daily)
    total_tokens = sum(d["total"] for d in ordered_daily)
    return {
        "days": days,
        "daily": ordered_daily,
        "models": sorted(models.values(), key=lambda m: -m["cost"]),
        "top_conversations": top_convs,
        "batches": batches,
        "totals": {
            "cost": round(total_cost, 6),
            "tokens": total_tokens,
            "messages": sum(d["messages"] for d in ordered_daily),
            "avg_cost_per_message": round(total_cost / sum(d["messages"] for d in ordered_daily), 6)
            if any(d["messages"] for d in ordered_daily) else 0.0,
        },
    }
