"""Tests for per-message (question/answer) deletion inside a dialogue:
soft delete + tombstone, sync pull exclusion, and push-resurrection guard.

Also covers the OpenAI Flex processing tier (":flex" model suffix) and its
automatic fallback to the standard tier when the provider rejects it.
"""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.openrouter import (  # noqa: E402
    chat_completion,
    is_flex_unsupported_error,
    split_model_variant,
)

client = TestClient(app)


def login() -> str:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {login()}"}


def _make_conversation_with_qa() -> tuple[dict, dict]:
    """Create a conversation holding one question and one answer via the API."""
    headers = auth_headers()
    conv = client.post(
        "/api/conversations", headers=headers, json={"title": "QA dialog"}
    ).json()
    q = client.post(
        f"/api/conversations/{conv['id']}/messages",
        headers=headers,
        json={"role": "user", "content": "What is 2+2?"},
    ).json()
    a = client.post(
        f"/api/conversations/{conv['id']}/messages",
        headers=headers,
        json={"role": "assistant", "content": "4", "model": "test/model"},
    ).json()
    return conv, {"question": q, "answer": a}


# ---------------------------------------------------------------------------
# Message (question/answer) deletion via the web
# ---------------------------------------------------------------------------

def test_delete_message_soft_deletes_and_excludes_from_pull():
    headers = auth_headers()
    conv, msgs = _make_conversation_with_qa()
    conv_id = conv["id"]

    resp = client.delete(
        f"/api/conversations/{conv_id}/messages/{msgs['question']['id']}",
        headers=headers,
    )
    assert resp.status_code == 204

    # The dialog still exists; the deleted question is gone from the detail.
    detail = client.get(f"/api/conversations/{conv_id}", headers=headers).json()
    remaining = [m["content"] for m in detail["messages"]]
    assert "What is 2+2?" not in remaining
    assert "4" in remaining
    assert len(remaining) == 1

    # Sync pull does not hand the deleted message to any device.
    pull = client.get("/api/sync/pull", headers=headers).json()
    synced = next(
        c
        for c in pull["conversations"]
        if c["external_id"] == f"srv-{conv_id}"  # web dialogs get srv- ids
    )
    synced_contents = [m["content"] for m in synced["messages"]]
    assert "What is 2+2?" not in synced_contents
    assert "4" in synced_contents


def test_delete_message_of_other_conversation_returns_404():
    headers = auth_headers()
    conv, msgs = _make_conversation_with_qa()
    other, _ = _make_conversation_with_qa()

    resp = client.delete(
        f"/api/conversations/{other['id']}/messages/{msgs['question']['id']}",
        headers=headers,
    )
    assert resp.status_code == 404


def test_phone_push_does_not_resurrect_deleted_message():
    """A device that still holds the pre-deletion dialog must not bring the
    deleted question back when it pushes its full local message list."""
    headers = auth_headers()
    conv, msgs = _make_conversation_with_qa()

    assert (
        client.delete(
            f"/api/conversations/{conv['id']}/messages/{msgs['question']['id']}",
            headers=headers,
        ).status_code
        == 204
    )

    # The phone's stale local copy still contains the deleted question.
    push_body = {
        "dialogs": [
            {
                "id": f"srv-{conv['id']}",  # external_id assigned on first pull
                "title": "QA dialog",
                "model": "test/model",
                "messages": [
                    {"role": "user", "content": "What is 2+2?"},
                    {"role": "assistant", "content": "4"},
                ],
            }
        ],
        "batches": [],
        "deleted_external_ids": [],
    }
    resp = client.post("/api/sync/push", headers=headers, json=push_body)
    assert resp.status_code == 200, resp.text

    detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
    contents = [m["content"] for m in detail["messages"]]
    assert "What is 2+2?" not in contents  # still deleted
    assert "4" in contents

    # The archived original text is still in the database (soft delete).
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Message

    db = SessionLocal()
    try:
        rows = db.scalars(
            select(Message).where(
                Message.conversation_id == conv["id"],
                Message.content == "What is 2+2?",
            )
        ).all()
        assert len(rows) == 1  # the archived copy survived
        assert rows[0].deleted_at is not None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Flex processing tier (":flex" model suffix)
# ---------------------------------------------------------------------------

def test_split_model_variant():
    assert split_model_variant("openai/gpt-6-astra:flex") == ("openai/gpt-6-astra", "flex")
    assert split_model_variant("openai/gpt-6-astra") == ("openai/gpt-6-astra", None)
    # ":batch" is part of the OpenRouter model id, kept intact
    assert split_model_variant("anthropic/claude-fable-5.1:batch") == (
        "anthropic/claude-fable-5.1:batch",
        "batch",
    )


def test_flex_unsupported_error_detection():
    assert is_flex_unsupported_error(400, "service_tier flex is not available for this model")
    assert is_flex_unsupported_error(400, "Flex processing is not supported")
    assert not is_flex_unsupported_error(400, "Unknown model: openai/gpt-6-astra")
    assert not is_flex_unsupported_error(404, "flex")


def test_chat_send_reports_web_search_used(monkeypatch):
    """web_search_used must be true only when Tavily results were injected,
    so the web UI can badge the question instead of surprising the user."""
    from app.routers import chat as chat_router

    headers = auth_headers()

    # Web search requested and Tavily returns results -> web_search_used true
    monkeypatch.setattr(
        chat_router.tavily, "is_configured", lambda: True
    )
    monkeypatch.setattr(
        chat_router.tavily,
        "search_web",
        lambda *a, **k: [{"title": "t", "url": "https://x", "content": "c"}],
    )
    monkeypatch.setattr(
        chat_router.tavily, "web_search_context", lambda *a, **k: "TEST CONTEXT"
    )
    monkeypatch.setattr(
        chat_router,
        "chat_completion",
        lambda model, messages, temperature=None, max_tokens=None: "with context",
    )

    resp = client.post(
        "/api/chat/send",
        headers=headers,
        json={"user_message": "search please", "models": ["openai/gpt-4o-mini"], "web_search": True},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["web_search_used"] is True

    # Web search off -> web_search_used false (no hidden injection)
    resp = client.post(
        "/api/chat/send",
        headers=headers,
        json={"user_message": "no search", "models": ["openai/gpt-4o-mini"], "web_search": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["web_search_used"] is False


def test_chat_send_injects_server_datetime(monkeypatch):
    """The system prompt must carry the current date/time from the server
    clock (phone-app parity) so time questions don't need web search."""
    from app.routers import chat as chat_router

    captured: dict = {}

    def fake_completion(model, messages, temperature=None, max_tokens=None):
        captured["messages"] = messages
        return "ok"

    monkeypatch.setattr(chat_router, "chat_completion", fake_completion)

    resp = client.post(
        "/api/chat/send",
        headers=auth_headers(),
        json={"user_message": "what time is it", "models": ["openai/gpt-4o-mini"]},
    )
    assert resp.status_code == 200, resp.text

    system = captured["messages"][0]
    assert system["role"] == "system"
    assert "Current date and time:" in system["content"]
    assert "reliable server clock" in system["content"]


def test_chat_completion_falls_back_when_flex_rejected(monkeypatch):
    """Astra without flex → HTTP 400 mentioning the tier → automatic retry on
    the standard tier returns the answer."""
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            calls.append(dict(json))  # snapshot: the code mutates the payload
            if json.get("service_tier") == "flex":
                return FakeResponse(
                    400,
                    {
                        "error": {
                            "message": "service_tier 'flex' is not supported for this model"
                        }
                    },
                )
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": "hello from astra"}}]},
            )

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    answer = chat_completion("openai/gpt-6-astra:flex", [{"role": "user", "content": "hi"}])
    assert answer == "hello from astra"
    assert len(calls) == 2
    assert calls[0]["service_tier"] == "flex"
    assert calls[0]["model"] == "openai/gpt-6-astra"
    assert "service_tier" not in calls[1]


def test_chat_completion_sends_base_model_for_plain_models(monkeypatch):
    """No tier suffix → single request, no service_tier field at all."""
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            calls.append(json)
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    answer = chat_completion("openai/gpt-6-astra", [{"role": "user", "content": "hi"}])
    assert answer == "ok"
    assert len(calls) == 1
    assert "service_tier" not in calls[0]