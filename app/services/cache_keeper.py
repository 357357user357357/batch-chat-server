"""Prompt-cache keep-alive.

Anthropic/OpenRouter prompt caches have a 1-hour maximum TTL, but every cache
READ refreshes that TTL. So after the last real chat request we periodically
send a near-empty request (a "." user message, max_tokens=8) that reproduces
the exact cached prefix — same system prompt, same history — and therefore
hits the cache at the cheap cache-read price (~10% of input) instead of
rewriting it. Each ping extends the cache another hour, so a cache written
once stays cheap for `cache_keepalive_hours` (default 3) after the last real
request instead of just one.

Cost per ping ≈ cache-read tokens (10%) + 8 output tokens. For a long
conversation this is far below one full-price re-request.

The exact system prompt of the last real request is registered via touch()
and reused verbatim for pings — a different system prompt would miss the
cache and pay full input price (the opposite of the goal).
"""

import logging
import threading
import time

from app.config import settings

logger = logging.getLogger("cache_keeper")

# Pings go out at most this often; safely below the 1-hour cache TTL.
PING_INTERVAL_SECONDS = 45 * 60
# How often the keeper thread checks for due pings.
_TICK_SECONDS = 60
# Output cap for pings — near-empty by design.
PING_MAX_TOKENS = 8

_lock = threading.Lock()
# conversation_id -> {"system": str, "models": [str], "last_activity": float,
#                     "last_ping": {model: float}}
_entries: dict[int, dict] = {}
_thread: threading.Thread | None = None


def touch(conversation_id: int, system: str, models: list[str]) -> None:
    """Register a real request so its cache gets kept warm.

    Only meaningful when a 1-hour cache is configured; with the 5-minute TTL
    the 45-minute ping interval could never hit, so we don't even track.
    """
    if settings.cache_keepalive_hours <= 0 or settings.cache_duration_seconds < 3600:
        return
    usable = [
        m for m in dict.fromkeys(models)
        if m and not m.endswith(":batch")  # ":batch" ids only work via the async Batch API
    ]
    if not usable:
        return
    now = time.time()
    with _lock:
        entry = _entries.get(conversation_id)
        if entry is None:
            entry = {"system": system, "models": [], "last_activity": now, "last_ping": {}}
            _entries[conversation_id] = entry
        entry["system"] = system
        entry["models"] = usable
        entry["last_activity"] = now
    logger.info("Keep-alive registered conv=%s models=%s", conversation_id, usable)


def start_cache_keeper() -> None:
    """Start the daemon ping loop (idempotent)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run, name="cache-keeper", daemon=True)
    _thread.start()
    logger.info("Cache keep-alive started (window=%sh, interval=%ss)",
                settings.cache_keepalive_hours, PING_INTERVAL_SECONDS)


def _run() -> None:
    while True:
        time.sleep(_TICK_SECONDS)
        try:
            _tick()
        except Exception:  # never let the keeper die
            logger.exception("Keep-alive tick failed")


def _tick() -> None:
    if settings.cache_keepalive_hours <= 0:
        return
    now = time.time()
    window = settings.cache_keepalive_hours * 3600
    with _lock:
        due: list[tuple[int, str]] = []
        for conversation_id, entry in list(_entries.items()):
            if now - entry["last_activity"] > window:
                del _entries[conversation_id]  # window over — let the cache expire
                continue
            for model in entry["models"]:
                if now - entry["last_ping"].get(model, 0.0) >= PING_INTERVAL_SECONDS:
                    due.append((conversation_id, model))
                    entry["last_ping"][model] = now  # claim immediately (no stampede)
    for conversation_id, model in due:
        _ping(conversation_id, model)


def _ping(conversation_id: int, model: str) -> None:
    """Send one near-empty request that hits the conversation's cached prefix."""
    try:
        from sqlalchemy import select

        from app.database import SessionLocal
        from app.models import Conversation, Message

        with _lock:
            entry = _entries.get(conversation_id)
            system = entry["system"] if entry else ""

        db = SessionLocal()
        try:
            conv = db.get(Conversation, conversation_id)
            if conv is None or conv.deleted_at is not None:
                with _lock:
                    _entries.pop(conversation_id, None)
                return
            history = [
                {"role": m.role, "content": m.content}
                for m in db.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.deleted_at.is_(None),
                    )
                    .order_by(Message.id)
                )
            ]
        finally:
            db.close()

        messages = [{"role": "system", "content": system}] + history
        messages.append({"role": "user", "content": "."})

        from app.services.openrouter import chat_completion

        chat_completion(model, messages, temperature=0, max_tokens=PING_MAX_TOKENS)
        logger.info("Keep-alive ping sent (conv=%s model=%s)", conversation_id, model)
    except Exception as exc:
        logger.warning("Keep-alive ping failed (conv=%s model=%s): %s",
                       conversation_id, model, exc)


def reset_for_tests() -> None:
    with _lock:
        _entries.clear()