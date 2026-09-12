"""Tests for the 🔄 retry endpoint: re-answer one assistant reply with other
model(s). Covers positional insertion (the new answers appear right after the
retried one), the history cutoff (the new model sees the context UP TO the
question, not the old answer), metadata persistence, the phone's external_id
path and the error cases.
"""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.provider_errors import ProviderError  # noqa: E402

client = TestClient(app)


def auth_headers() -> dict:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _make_dialog(headers: dict) -> tuple[int, int, int]:
    """Q1 → A1 → Q2 → A2 via the API. Returns (conv_id, a1_id, a2_id)."""
    conv = client.post(
        "/api/conversations", headers=headers, json={"title": "retry dlg"}
    ).json()
    cid = conv["id"]

    def add(role: str, content: str, model: str | None = None) -> dict:
        return client.post(
            f"/api/conversations/{cid}/messages",
            headers=headers,
            json={"role": role, "content": content, "model": model},
        ).json()

    q1 = add("user", "What is the capital of France?")
    a1 = add("assistant", "Paris.", model="test/model-a")
    q2 = add("user", "And its population?")
    a2 = add("assistant", "About 2 million.", model="test/model-a")
    return cid, a1["id"], a2["id"], q1["id"]


def test_retry_reanswers_in_place_with_context_cutoff(monkeypatch):
    headers = auth_headers()
    cid, a1_id, _a2_id, q1_id = _make_dialog(headers)

    captured: dict = {}

    def fake_completion(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        captured["model"] = model
        captured["messages"] = messages
        return {"content": "Paris (retried)."}

    from app.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "chat_completion_full", fake_completion)

    resp = client.post(
        "/api/chat/retry",
        headers=headers,
        json={
            "conversation_id": cid,
            "message_id": a1_id,
            "models": ["other/model-b"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["conversation_id"] == cid
    assert data["source_message_id"] == a1_id
    assert data["responses"][0]["ok"] is True
    new_id = data["responses"][0]["message_id"]
    assert new_id

    # The model was asked ONLY the question (no old answer, no later turns).
    assert captured["model"] == "other/model-b"
    roles_contents = [(m["role"], m["content"]) for m in captured["messages"]]
    assert ("user", "What is the capital of France?") in roles_contents
    assert ("assistant", "Paris.") not in roles_contents  # old answer excluded
    assert ("user", "And its population?") not in roles_contents  # after cutoff
    # System prompt (server clock) still prepended.
    assert captured["messages"][0]["role"] == "system"
    assert "Current date and time:" in captured["messages"][0]["content"]

    # Positional order: the fresh answer sits right after the retried one.
    detail = client.get(f"/api/conversations/{cid}", headers=headers).json()
    contents = [m["content"] for m in detail["messages"]]
    assert contents == [
        "What is the capital of France?",
        "Paris.",
        "Paris (retried).",
        "And its population?",
        "About 2 million.",
    ]
    by_id = {m["id"]: m for m in detail["messages"]}
    assert by_id[new_id]["model"] == "other/model-b"


def test_retry_persists_openrouter_metadata(monkeypatch):
    """The retried answer keeps the full per-message OpenRouter metadata
    (provider, generation id, token usage, cost) like a normal send does."""
    headers = auth_headers()
    cid, a1_id, _a2_id, _q1_id = _make_dialog(headers)

    from app.routers import chat as chat_router

    monkeypatch.setattr(
        chat_router,
        "chat_completion_full",
        lambda model, messages, temperature=None, max_tokens=None, reasoning_effort=None: {
            "content": "meta answer",
            "provider": "TestProvider",
            "gen_id": "gen-retry-1",
            "tokens_prompt": 11,
            "tokens_completion": 7,
            "total_tokens": 18,
            "cost": 0.00042,
        },
    )

    resp = client.post(
        "/api/chat/retry",
        headers=headers,
        json={"conversation_id": cid, "message_id": a1_id,
              "models": ["other/model-b"], "reasoning_effort": "high"},
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["responses"][0]
    assert item["reasoning"] == "high"
    assert item["provider"] == "TestProvider"
    assert item["gen_id"] == "gen-retry-1"
    assert item["tokens_prompt"] == 11 and item["tokens_completion"] == 7
    assert item["total_tokens"] == 18 and item["cost"] == 0.00042

    detail = client.get(f"/api/conversations/{cid}", headers=headers).json()
    stored = next(m for m in detail["messages"] if m["content"] == "meta answer")
    assert stored["provider"] == "TestProvider"
    assert stored["gen_id"] == "gen-retry-1"
    assert stored["reasoning"] == "high"
    assert stored["total_tokens"] == 18 and stored["cost"] == 0.00042


def test_retry_multiple_models_ordered_between_neighbors(monkeypatch):
    headers = auth_headers()
    cid, a1_id, _a2_id, _q1_id = _make_dialog(headers)

    answers = {"model/x": "X answer", "model/y": "Y answer"}

    def fake_completion(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        return {"content": answers[model]}

    from app.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "chat_completion_full", fake_completion)

    resp = client.post(
        "/api/chat/retry",
        headers=headers,
        json={"conversation_id": cid, "message_id": a1_id,
              "models": ["model/x", "model/y"]},
    )
    assert resp.status_code == 200, resp.text

    detail = client.get(f"/api/conversations/{cid}", headers=headers).json()
    contents = [m["content"] for m in detail["messages"]]
    assert contents == [
        "What is the capital of France?",
        "Paris.",
        "X answer",
        "Y answer",
        "And its population?",
        "About 2 million.",
    ]


def test_retry_via_external_id_phone_path(monkeypatch):
    """The phone retries by sync external_id + message serverId; the new
    answer lands right after the original in the next pull."""
    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "test-phone"}
    assert client.post("/api/sync/push", headers=phone_headers, json={
        "dialogs": [{"id": "retry-ext-dlg", "title": "Ext", "model": "m",
                     "messages": [{"role": "user", "content": "2+2?"},
                                  {"role": "assistant", "content": "4"}]}],
        "batches": [], "deleted_external_ids": [], "keys": {}}).status_code == 200

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    dlg = next(c for c in pulled if c["external_id"] == "retry-ext-dlg")
    answer_id = next(m["id"] for m in dlg["messages"] if m["role"] == "assistant")

    from app.routers import chat as chat_router

    monkeypatch.setattr(
        chat_router, "chat_completion_full",
        lambda model, messages, temperature=None, max_tokens=None, reasoning_effort=None:
            {"content": "four"},
    )

    resp = client.post(
        "/api/chat/retry",
        headers=phone_headers,
        json={"external_id": "retry-ext-dlg", "message_id": answer_id,
              "models": ["other/model-c"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["external_id"] == "retry-ext-dlg"

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    dlg = next(c for c in pulled if c["external_id"] == "retry-ext-dlg")
    contents = [m["content"] for m in dlg["messages"]]
    # Order is what matters: the fresh answer directly follows the original.
    assert contents == ["2+2?", "4", "four"]
    models = [m["model"] for m in dlg["messages"]]
    assert models[-1] == "other/model-c"

    # Cleanup (hard, test-only).
    import sqlite3

    from app.database import engine

    with sqlite3.connect(engine.url.database) as conn:
        conv_id = conn.execute(
            "SELECT id FROM conversations WHERE external_id='retry-ext-dlg'"
        ).fetchone()[0]
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def test_retry_failed_model_persists_nothing(monkeypatch):
    headers = auth_headers()
    cid, a1_id, _a2_id, _q1_id = _make_dialog(headers)
    before = client.get(f"/api/conversations/{cid}", headers=headers).json()["messages"]

    from app.routers import chat as chat_router

    def boom(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        raise ProviderError("Model unavailable")

    monkeypatch.setattr(chat_router, "chat_completion_full", boom)

    resp = client.post(
        "/api/chat/retry",
        headers=headers,
        json={"conversation_id": cid, "message_id": a1_id, "models": ["dead/model"]},
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["responses"][0]
    assert item["ok"] is False
    assert item["message_id"] is None
    assert "Model" in item["error"] or "error" in item["error"].lower()

    after = client.get(f"/api/conversations/{cid}", headers=headers).json()["messages"]
    assert [m["id"] for m in after] == [m["id"] for m in before]  # nothing added


def test_retry_error_cases():
    headers = auth_headers()
    cid, a1_id, _a2_id, q1_id = _make_dialog(headers)

    # Neither dialog identifier -> 422.
    resp = client.post(
        "/api/chat/retry", headers=headers,
        json={"message_id": a1_id, "models": ["m"]},
    )
    assert resp.status_code == 422

    # Retrying a question (not an answer) -> 400.
    resp = client.post(
        "/api/chat/retry", headers=headers,
        json={"conversation_id": cid, "message_id": q1_id, "models": ["m"]},
    )
    assert resp.status_code == 400

    # Unknown message / dialog -> 404.
    assert client.post(
        "/api/chat/retry", headers=headers,
        json={"conversation_id": cid, "message_id": 999999, "models": ["m"]},
    ).status_code == 404
    assert client.post(
        "/api/chat/retry", headers=headers,
        json={"conversation_id": 999999, "message_id": a1_id, "models": ["m"]},
    ).status_code == 404
    assert client.post(
        "/api/chat/retry", headers=headers,
        json={"external_id": "no-such-dialog", "message_id": a1_id, "models": ["m"]},
    ).status_code == 404

    # Another account's dialog is invisible -> 404 (isolation).
    created = client.post(
        "/api/auth/accounts",
        json={"admin_password": "test", "label": "retryiso", "client_password": "iso-pw-1"},
    )
    other = _pair_headers(created.json()["pair_code"])
    assert client.post(
        "/api/chat/retry", headers=other,
        json={"conversation_id": cid, "message_id": a1_id, "models": ["m"]},
    ).status_code == 404


def _pair_headers(pair_code: str) -> dict:
    resp = client.post("/api/auth/pair", json={"code": pair_code})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_retry_answer_after_web_search_still_searches(monkeypatch):
    """web_search on retry injects fresh Tavily context, exactly like send."""
    headers = auth_headers()
    cid, a1_id, _a2_id, _q1_id = _make_dialog(headers)

    from app.routers import chat as chat_router

    monkeypatch.setattr(chat_router.tavily, "is_configured", lambda: True)
    monkeypatch.setattr(
        chat_router.tavily, "search_web",
        lambda *a, **k: [{"title": "t", "url": "https://x", "content": "c"}],
    )
    monkeypatch.setattr(chat_router.tavily, "web_search_context", lambda *a, **k: "RETRY CONTEXT")

    captured: dict = {}

    def fake_completion(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        captured["system"] = messages[0]["content"]
        return {"content": "with web"}

    monkeypatch.setattr(chat_router, "chat_completion_full", fake_completion)

    resp = client.post(
        "/api/chat/retry", headers=headers,
        json={"conversation_id": cid, "message_id": a1_id,
              "models": ["m"], "web_search": True},
    )
    assert resp.status_code == 200, resp.text
    assert "RETRY CONTEXT" in captured["system"]
