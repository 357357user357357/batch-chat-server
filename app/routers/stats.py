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
from app.models import Conversation, Message, utcnow
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
