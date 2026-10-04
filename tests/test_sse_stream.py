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


def test_send_kind_flag_files_conversation(monkeypatch):
    """Web ⚡ Batch mode tags new conversations kind=batch so the phone files
    them under its Batch tab (they share the same Conversation table);
    default (live/flex) stays kind=chat."""
    from app.routers import chat as chat_router

    monkeypatch.setattr(
        chat_router,
        "chat_completion_full",
        lambda model, messages, temperature=None, max_tokens=None,
        reasoning_effort=None: {"content": "ok"},
    )

    login = client.post("/api/auth/login", json={"password": "test"}).json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    for kind, expected in (("batch", "batch"), (None, "chat")):
        body = {"user_message": "kind check", "models": ["m/1"]}
        if kind:
            body["kind"] = kind
        resp = client.post("/api/chat/send", headers=headers, json=body)
        assert resp.status_code == 200, resp.text
        payload = json.loads(
            [ln[len("data: "):] for ln in resp.text.splitlines()
             if ln.startswith("data: ")][-1]
        )
        convs = client.get("/api/conversations", headers=headers).json()
        match = [c for c in convs if c["id"] == payload["conversation_id"]]
        assert match, f"conversation {payload['conversation_id']} not listed"
        assert match[0]["kind"] == expected

    # Unknown kinds are rejected by the schema.
    bad = client.post(
        "/api/chat/send", headers=headers,
        json={"user_message": "x", "models": ["m/1"], "kind": "weird"},
    )
    assert bad.status_code == 422


def test_send_kind_refiles_existing_conversation(monkeypatch):
    """The web mode toggle is global: a ⚡ Batch message sent into an existing
    kind=chat conversation re-files it kind=batch (phone moves it to its Batch
    tab on the next pull), and a live/flex message flips it back to chat."""
    from app.routers import chat as chat_router

    monkeypatch.setattr(
        chat_router,
        "chat_completion_full",
        lambda model, messages, temperature=None, max_tokens=None,
        reasoning_effort=None: {"content": "ok"},
    )

    login = client.post("/api/auth/login", json={"password": "test"}).json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    resp = client.post(
        "/api/chat/send", headers=headers,
        json={"user_message": "started in live mode", "models": ["m/1"]},
    )
    assert resp.status_code == 200, resp.text
    conv_id = json.loads(
        [ln[len("data: "):] for ln in resp.text.splitlines()
         if ln.startswith("data: ")][-1]
    )["conversation_id"]

    for kind, expected in (("batch", "batch"), ("batch", "batch"), ("chat", "chat")):
        resp = client.post("/api/chat/send", headers=headers, json={
            "user_message": "mode flip", "models": ["m/1"],
            "conversation_id": conv_id, "kind": kind,
        })
        assert resp.status_code == 200, resp.text
        convs = client.get("/api/conversations", headers=headers).json()
        match = [c for c in convs if c["id"] == conv_id]
        assert match, f"conversation {conv_id} not listed"
        assert match[0]["kind"] == expected


def test_web_batch_survives_stale_phone_push_and_stays_in_phone_batch_tab(monkeypatch):
    """A multi-model web batch must remain a batch after a phone pushes an old
    local chat copy, and every parallel answer must be present in the pull."""
    from app.routers import chat as chat_router

    def fake_full(model, messages, temperature=None, max_tokens=None,
                  reasoning_effort=None):
        return {"content": f"answer from {model}"}

    monkeypatch.setattr(chat_router, "chat_completion_full", fake_full)
    login = client.post("/api/auth/login", json={"password": "test"}).json()
    web_headers = {"Authorization": f"Bearer {login['token']}"}
    phone_headers = {**web_headers, "X-Device-Name": "regression-phone"}

    # The phone has already synced a normal-chat copy, establishing an
    # incremental cursor and an older local snapshot.
    first = client.post(
        "/api/chat/send",
        headers=web_headers,
        json={"user_message": "existing phone chat", "models": ["model/a"]},
    )
    assert first.status_code == 200, first.text
    first_payload = json.loads(
        [line[len("data: "):]
         for line in first.text.splitlines() if line.startswith("data: ")][-1]
    )
    conv_id = first_payload["conversation_id"]
    external_id = f"srv-{conv_id}"
    baseline = client.get("/api/sync/pull", headers=phone_headers)
    assert baseline.status_code == 200, baseline.text
    old_copy = next(
        c for c in baseline.json()["conversations"] if c["external_id"] == external_id
    )
    assert old_copy["kind"] == "chat"

    # The web re-files that existing dialog as batch and stores two parallel
    # model answers after the phone's last pull.
    resp = client.post(
        "/api/chat/send",
        headers=web_headers,
        json={
            "user_message": "web batch sync regression",
            "models": ["model/a", "model/b"],
            "conversation_id": conv_id,
            "kind": "batch",
        },
    )
    assert resp.status_code == 200, resp.text

    pushed = client.post(
        "/api/sync/push",
        headers=phone_headers,
        json={
            "dialogs": [{
                "id": external_id,
                "title": old_copy["title"],
                "model": old_copy["model"],
                "messages": [
                    {"role": message["role"], "content": message["content"],
                     "model": message["model"]}
                    for message in old_copy["messages"]
                ],
            }],
            "batches": [],
            "deleted_external_ids": [],
        },
    )
    assert pushed.status_code == 200, pushed.text

    since = baseline.json()["server_time"]
    pulled = client.get(
        "/api/sync/pull", headers=phone_headers, params={"since": since}
    )
    assert pulled.status_code == 200, pulled.text
    target = next(c for c in pulled.json()["conversations"] if c["external_id"] == external_id)
    assert target["kind"] == "batch"
    assert {m["content"] for m in target["messages"] if m["role"] == "assistant"} == {
        "answer from model/a",
        "answer from model/b",
    }