"""JSONL → OpenRouter batch request converter.

Understands the .jsonl line formats used by the big cloud providers plus the
plain OpenRouter/OpenAI one, so you can feed almost any existing batch file:

  # 1. OpenRouter / OpenAI batch format
  {"custom_id": "req-1", "body": {"messages": [{"role": "user", "content": "..."}]}}
  {"custom_id": "req-2", "body": {"messages": [...], "temperature": 0.2}}

  # 2. Shorthand messages
  {"custom_id": "req-1", "messages": [{"role": "user", "content": "..."}]}

  # 3. Simple prompt
  {"custom_id": "req-1", "prompt": "Write a haiku"}

  # 4. Google Vertex AI (Gemini batch prediction JSONL)
  {"request": {"contents": [{"role": "user", "parts": [{"text": "Write a haiku"}]}]}}
  {"request": {"systemInstruction": {"parts": [{"text": "You are a poet"}]},
                "contents": [...]}}

  # 5. AWS Bedrock (Anthropic-style modelInput)
  {"recordId": "rec-1", "modelInput": {"system": "You are a poet",
                                        "messages": [{"role": "user",
                                                      "content": "Write a haiku"}]}}
"""

import json

BatchRequest = dict  # {"custom_id": str, "body": {"messages": [...], ...}}


class JsonlParseError(ValueError):
    pass


def parse_jsonl(
    text: str,
    *,
    system: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> list[BatchRequest]:
    """Parse a .jsonl document into OpenRouter batch requests.

    Raises JsonlParseError with the offending line number on bad input.
    """
    requests: list[BatchRequest] = []
    line_number = 0
    for raw_line in text.splitlines():
        line_number += 1
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JsonlParseError(f"Line {line_number}: invalid JSON — {exc.msg}") from exc

        request = _line_to_request(obj)

        body = request["body"]
        if system and not _has_system_role(body.get("messages", [])):
            body["messages"] = [{"role": "system", "content": system}] + body.get("messages", [])
        if temperature is not None and "temperature" not in body:
            body["temperature"] = temperature
        if max_tokens is not None and "max_tokens" not in body:
            body["max_tokens"] = max_tokens
        requests.append(request)

    if not requests:
        raise JsonlParseError("No requests parsed from the JSONL (empty file?)")
    return requests


def _line_to_request(obj: dict) -> BatchRequest:
    if not isinstance(obj, dict):
        raise JsonlParseError("each line must be a JSON object")

    # 4. Google Vertex AI style
    if "request" in obj:
        return _from_vertex(obj["request"], obj.get("custom_id"))

    # 5. AWS Bedrock style
    if "recordId" in obj and "modelInput" in obj:
        return _from_bedrock(obj["recordId"], obj["modelInput"])

    custom_id = obj.get("custom_id") or obj.get("id")
    custom_id = str(custom_id) if custom_id else None

    # 1. OpenRouter / OpenAI: {custom_id, body: {messages, ...}}
    body = obj.get("body")
    if isinstance(body, dict):
        messages = _normalize_messages(body.get("messages"))
        merged = dict(body)
        merged["messages"] = messages
        merged.pop("model", None)  # batch-level model governs
        return {"custom_id": custom_id or _auto_id(obj.get("prompt")),
                "body": merged}

    # 2. Shorthand: {custom_id, messages}
    if "messages" in obj and isinstance(obj["messages"], list):
        return {"custom_id": custom_id or _auto_id(None),
                "body": {"messages": _normalize_messages(obj["messages"])}}

    # 3. Simple prompt
    prompt = obj.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return {"custom_id": custom_id or _auto_id(prompt),
                "body": {"messages": [{"role": "user", "content": prompt}]}}

    raise JsonlParseError("unsupported line format (expected body/messages/prompt/request/modelInput)")


def _from_vertex(request_body, custom_id) -> BatchRequest:
    if not isinstance(request_body, dict):
        raise JsonlParseError("'request' must be an object")

    messages: list[dict] = []

    system_instruction = request_body.get("systemInstruction")
    if isinstance(system_instruction, dict):
        sys_text = _parts_to_text(system_instruction.get("parts"))
        if sys_text:
            messages.append({"role": "system", "content": sys_text})

    contents = request_body.get("contents")
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "user")
            if role == "model":
                role = "assistant"
            content = _parts_to_text(item.get("parts")) or str(item.get("text") or "")
            if content.strip():
                messages.append({"role": role, "content": content})

    # PaLM style fallback: {"request": {"prompt": "..."}}
    if not messages:
        prompt = request_body.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            messages.append({"role": "user", "content": prompt})

    if not messages:
        raise JsonlParseError("'request' has no usable contents/prompt")

    return {"custom_id": str(custom_id) if custom_id else _auto_id(None),
            "body": {"messages": messages}}


def _from_bedrock(record_id, model_input: dict) -> BatchRequest:
    if not isinstance(model_input, dict):
        raise JsonlParseError("'modelInput' must be an object")

    messages: list[dict] = []

    system = model_input.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})

    for msg in model_input.get("messages", []) or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = _flatten_anthropic_content(msg.get("content"))
        if content.strip():
            messages.append({"role": role, "content": content})

    if not messages:
        raise JsonlParseError("'modelInput' has no usable messages")

    return {"custom_id": str(record_id) if record_id else _auto_id(None),
            "body": {"messages": messages}}


def _parts_to_text(parts) -> str:
    if not isinstance(parts, list):
        return ""
    chunks = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    return "\n".join(chunks)


def _flatten_anthropic_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks)
    return ""


def _normalize_messages(messages) -> list[dict]:
    if not isinstance(messages, list):
        raise JsonlParseError("'messages' must be a list")
    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content")
        if isinstance(content, list):
            content = _flatten_anthropic_content(content)
        if not isinstance(content, str):
            content = str(content) if content else ""
        if role == "model":
            role = "assistant"
        out.append({"role": role, "content": content})
    if not out:
        raise JsonlParseError("'messages' is empty")
    return out


def _has_system_role(messages: list[dict]) -> bool:
    return any(m.get("role") == "system" for m in messages)


_counter = [0]


def _auto_id(prompt: str | None) -> str:
    _counter[0] += 1
    base = "req"
    if prompt:
        cleaned = " ".join(prompt.split())[:24].lower().replace(" ", "-")
        if cleaned:
            base = cleaned
    return f"{base}-{_counter[0]}"