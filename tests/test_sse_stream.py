"""Tests for the OpenRouter SSE stream folding (parse_sse_chat_stream) and
the streaming chat transport.

Chat answers are fetched with "stream": true so OpenRouter's keep-alive
comments (": OPENROUTER PROCESSING") keep the upstream connection alive while
a ":flex" request queues; the web endpoints stream `: ping` keep-alives to
the browser for the same reason. These tests pin both halves.
"""

import json
import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.openrouter import parse_sse_chat_stream  # noqa: E402

client = TestClient(app)


def test_folds_deltas_keepalives_and_usage():
    lines = [
        ": OPENROUTER PROCESSING",
        "",
        'data: {"id": "gen-1", "provider": "Novita", '
        '"choices": [{"delta": {"role": "assistant", "content": "Hel"}}]}',
        ": OPENROUTER PROCESSING",
        'data: {"choices": [{"delta": {"content": "lo world"}}]}',
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}],'
        ' "usage": {"prompt_tokens": 10, "completion_tokens": 3,'
        ' "total_tokens": 13, "cost": 0.01,'
        ' "prompt_tokens_details": {"cached_tokens": 4}}}',
        "data: [DONE]",
    ]
    data = parse_sse_chat_stream(lines)
    assert data["content"] == "Hello world"
    assert data["provider"] == "Novita"
    assert data["gen_id"] == "gen-1"
    assert data["tokens_prompt"] == 10
    assert data["tokens_cached"] == 4
    assert data["tokens_completion"] == 3
    assert data["total_tokens"] == 13
    assert data["cost"] == 0.01


def test_tolerates_bytes_and_malformed_lines():
    lines = [
        b": ping",
        b"data: {not json",
        b'data: {"choices": [{"delta": {"content": "ok"}}]}',
        "data: [DONE]",
        'data: {"choices": [{"delta": {"content": "IGNORED"}}]}',  # after DONE
    ]
    assert parse_sse_chat_stream(lines)["content"] == "ok"


def test_empty_stream_yields_none_metadata():
    data = parse_sse_chat_stream([": OPENROUTER PROCESSING", "data: [DONE]"])
    assert data["content"] == ""
    assert data["provider"] is None
    assert data["gen_id"] is None
    assert data["tokens_prompt"] is None


def test_chat_endpoint_streams_ping_then_payload(monkeypatch):
    """/api/chat/send must answer as an SSE stream: ping comment(s) first so
    the browser connection never sits silent, then the full ChatResponse as
    one data: event."""
    from app.routers import chat as chat_router

    monkeypatch.setattr(
        chat_router,
        "chat_completion_full",
        lambda model, messages, temperature=None, max_tokens=None,
        reasoning_effort=None: {"content": "streamed hello"},
    )

    login = client.post("/api/auth/login", json={"password": "test"}).json()
    headers = {"Authorization": f"Bearer {login['token']}"}
    conv = client.post(
        "/api/conversations", headers=headers, json={"title": "sse dlg"}
    ).json()

    resp = client.post(
        "/api/chat/send",
        headers=headers,
        json={"user_message": "hi", "models": ["m/1"],
              "conversation_id": conv["id"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert ": ping" in resp.text  # keep-alive emitted before/while models run
    events = [
        line[len("data: "):]
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(events) == 1  # exactly one payload event, pings are comments
    payload = json.loads(events[-1])
    assert payload["responses"][0]["content"] == "streamed hello"
    assert payload["responses"][0]["ok"] is True
    assert payload["user_message"]["content"] == "hi"