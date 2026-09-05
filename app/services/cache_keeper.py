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
# conversation_ids the user explicitly enabled via the 🔥 Cache toggle —
# only these are ever pinged. Persisted in app_settings by the router.
_enabled: set[int] = set()
_thread: threading.Thread | None = None


def set_enabled(conversation_id: int, enabled: bool) -> None:
    """Turn keep-alive warming on/off for one conversation (user toggle)."""
    with _lock:
        if enabled:
            _enabled.add(conversation_id)
        else:
            _enabled.discard(conversation_id)
            _entries.pop(conversation_id, None)
    logger.info("Keep-alive %s for conv=%s", "ENABLED" if enabled else "disabled", conversation_id)


def restore_enabled(ids: list[int]) -> None:
    """Re-apply the enabled set after a restart (loaded from app_settings)."""
    with _lock:
        _enabled.update(int(i) for i in ids)


def _is_enabled(conversation_id: int) -> bool:
    return conversation_id in _enabled


def record(conversation_id: int, system: str, models: list[str]) -> None:
    """Register the exact prefix of a real request (system + models).

    Recording alone never pings — warming happens only for conversations the
    user explicitly enabled via the 🔥 Cache toggle (see set_enabled).
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
    if _is_enabled(conversation_id):
        logger.info("Keep-alive (enabled) registered conv=%s models=%s", conversation_id, usable)


# Alias: every real request RECORDS its exact prefix (so warming can be
# enabled later), but only explicitly enabled conversations are ever pinged.
touch = record


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
            if conversation_id not in _enabled:
                continue  # warming is opt-in per dialog (🔥 Cache toggle)
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
        _enabled.clear()