"""Shared helpers for converting Android-app AsyncStorage payloads
(PhoneDialog / PhoneBatch) into message dicts (role/content/model plus the
optional OpenRouter metadata: reasoning, provider, gen_id, usage, cost).

Used by both the one-shot "paste an export" import endpoint and the
multi-device sync endpoints, so the two stay consistent.
"""

from app.schemas import PhoneBatch, PhoneDialog

_META_FIELDS = (
    "reasoning", "provider", "gen_id",
    "tokens_prompt", "tokens_completion", "total_tokens", "cost",
)


def title_default(item_title: str | None, fallback: str) -> str:
    return (item_title or fallback)[:255]


def _message_dict(role: str, content: str, model: str | None,
                  source=None) -> dict:
    msg = {"role": role, "content": content, "model": model}
    if source is not None:
        for field in _META_FIELDS:
            value = getattr(source, field, None)
            if value is not None:
                msg[field] = value
    return msg


def dialog_messages(dialog: PhoneDialog) -> list[dict]:
    return [
        _message_dict(m.role, m.content, m.model or dialog.model, source=m)
        for m in dialog.messages
    ]


def batch_messages(item: PhoneBatch) -> list[dict]:
    """Flatten a batch item into (prompt, answer) message pairs.

    Results are matched to prompts by custom_id ``req-{n}`` (1-based, same as
    the app's CSV export). Failed jobs become assistant messages prefixed
    with "[error] ".
    """
    answers: dict[str, str] = {}
    raw_results = (item.batch or {}).get("results") or []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        custom_id = str(result.get("custom_id") or "")
        if result.get("error"):
            answers[custom_id] = f"[error] {result.get('error')}"
            continue
        if result.get("ok") is False:
            answers[custom_id] = f"[error] HTTP {result.get('status', '?')}"
            continue
        body = result.get("response") or {}
        content = body.get("body", {}) if isinstance(body, dict) else {}
        answer = ""
        if isinstance(content, dict):
            choices = content.get("choices") or []
            if choices:
                answer = str(
                    choices[0].get("message", {}).get("content", "") or ""
                )
        answers[custom_id] = answer

    messages: list[dict] = []
    for index, prompt in enumerate(item.prompts, start=1):
        if not prompt or not prompt.strip():
            continue
        messages.append({"role": "user", "content": prompt, "model": None})
        answer = answers.get(f"req-{index}", "")
        if answer:
            messages.append({"role": "assistant", "content": answer, "model": item.model})
    return messages


def batch_label(prompts: list[str]) -> str:
    first = next((p for p in prompts if p and p.strip()), "") or "Batch chat"
    cleaned = " ".join(first.split())
    return cleaned[:42] + ("…" if len(cleaned) > 42 else "")


def collapse_repeated_blocks(messages: list[dict], min_repeats: int = 3) -> list[dict]:
    """Collapse a tail that repeats the same block of messages ``min_repeats``+ times.

    Flood guard for every ingest path (sync push, phone import). A buggy
    client release once synced its whole dialog list with the same dialog
    appended over and over, so a single push stored the identical Q/A pair
    32 times in one conversation — and every device kept receiving the
    copies from then on. When the message list is a (possibly empty) prefix
    followed by the same block repeated at least ``min_repeats`` times, keep
    one copy of the block and drop the repetitions.

    Messages are compared by (role, content) — the same identity the sync
    merge and the tombstones use. A user legitimately repeating one
    identical question once (two copies) is left untouched.
    """
    ids = [(m.get("role"), m.get("content")) for m in messages]
    n = len(ids)
    for offset in range(0, n - min_repeats + 1):
        tail = n - offset
        for block in range(1, tail // min_repeats + 1):
            if tail % block:
                continue
            head = ids[offset:offset + block]
            if all(ids[i] == head[(i - offset) % block] for i in range(offset, n)):
                return messages[:offset + block]
    return messages
