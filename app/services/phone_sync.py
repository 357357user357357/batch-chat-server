"""Shared helpers for converting Android-app AsyncStorage payloads
(PhoneDialog / PhoneBatch) into (role, content, model) message tuples.

Used by both the one-shot "paste an export" import endpoint and the
multi-device sync endpoints, so the two stay consistent.
"""

from app.schemas import PhoneBatch, PhoneDialog


def title_default(item_title: str | None, fallback: str) -> str:
    return (item_title or fallback)[:255]


def dialog_messages(dialog: PhoneDialog) -> list[tuple[str, str, str | None]]:
    return [(m.role, m.content, m.model or dialog.model) for m in dialog.messages]


def batch_messages(item: PhoneBatch) -> list[tuple[str, str, str | None]]:
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

    messages: list[tuple[str, str, str | None]] = []
    for index, prompt in enumerate(item.prompts, start=1):
        if not prompt or not prompt.strip():
            continue
        messages.append(("user", prompt, None))
        answer = answers.get(f"req-{index}", "")
        if answer:
            messages.append(("assistant", answer, item.model))
    return messages


def batch_label(prompts: list[str]) -> str:
    first = next((p for p in prompts if p and p.strip()), "") or "Batch chat"
    cleaned = " ".join(first.split())
    return cleaned[:42] + ("…" if len(cleaned) > 42 else "")
