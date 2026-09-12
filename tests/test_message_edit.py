"""Tests for ✏️ editing your own questions (web PATCH + phone sync PATCH):
in-place content update, tombstoning the old wording so stale device pushes
can't resurrect it, answers being non-editable, and retry-after-edit picking
up the new text.
"""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def auth_headers() -> dict:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _make_dialog(headers: dict) -> tuple[int, int, int]:
    """Q → A via the API. Returns (conv_id, question_id, answer_id)."""
    conv = client.post(
        "/api/conversations", headers=headers, json={"title": "edit dlg"}
    ).json()
    cid = conv["id"]
    q = client.post(
        f"/api/conversations/{cid}/messages", headers=headers,
        json={"role": "user", "content": "What is 2+2?"},
    ).json()
    a = client.post(
        f"/api/conversations/{cid}/messages", headers=headers,
        json={"role": "assistant", "content": "4", "model": "test/model-a"},
    ).json()
    return cid, q["id"], a["id"]


def test_edit_question_updates_content_in_place():
    headers = auth_headers()
    cid, q_id, _a_id = _make_dialog(headers)

    resp = client.patch(
        f"/api/conversations/{cid}/messages/{q_id}", headers=headers,
        json={"content": "What is 2+3?"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == "What is 2+3?"

    detail = client.get(f"/api/conversations/{cid}", headers=headers).json()
    contents = [m["content"] for m in detail["messages"]]
    assert contents == ["What is 2+3?", "4"]


def test_edit_answers_rejected_and_missing_404():
    headers = auth_headers()
    cid, _q_id, a_id = _make_dialog(headers)

    # Answers are model output — only questions are editable.
    assert client.patch(
        f"/api/conversations/{cid}/messages/{a_id}", headers=headers,
        json={"content": "nope"},
    ).status_code == 400
    assert client.patch(
        f"/api/conversations/{cid}/messages/999999", headers=headers,
        json={"content": "nope"},
    ).status_code == 404
    assert client.patch(
        "/api/conversations/999999/messages/1", headers=headers,
        json={"content": "nope"},
    ).status_code == 404


def test_edit_via_sync_path_and_stale_push_cannot_resurrect():
    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "test-phone"}
    assert client.post("/api/sync/push", headers=phone_headers, json={
        "dialogs": [{"id": "edit-ext-dlg", "title": "Edit", "model": "m",
                     "messages": [{"role": "user", "content": "old question"},
                                  {"role": "assistant", "content": "old answer"}]}],
        "batches": [], "deleted_external_ids": [], "keys": {}}).status_code == 200
    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    dlg = next(c for c in pulled if c["external_id"] == "edit-ext-dlg")
    q_id = next(m["id"] for m in dlg["messages"] if m["role"] == "user")

    resp = client.patch(
        "/api/sync/dialogs/edit-ext-dlg/messages/%d" % q_id,
        headers=phone_headers,
        json={"content": "edited question"},
    )
    assert resp.status_code == 200, resp.text

    # The phone's stale local copy still carries the old text — pushing it
    # must NOT re-append the old question after the edit (tombstone).
    assert client.post("/api/sync/push", headers=phone_headers, json={
        "dialogs": [{"id": "edit-ext-dlg", "title": "Edit", "model": "m",
                     "messages": [{"role": "user", "content": "old question"},
                                  {"role": "assistant", "content": "old answer"}]}],
        "batches": [], "deleted_external_ids": [], "keys": {}}).status_code == 200

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    dlg = next(c for c in pulled if c["external_id"] == "edit-ext-dlg")
    contents = [m["content"] for m in dlg["messages"]]
    assert contents == ["edited question", "old answer"]

    # Cleanup (hard, test-only).
    import sqlite3

    from app.database import engine

    with sqlite3.connect(engine.url.database) as conn:
        conv_id = conn.execute(
            "SELECT id FROM conversations WHERE external_id='edit-ext-dlg'"
        ).fetchone()[0]
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute(
            "DELETE FROM message_tombstones WHERE conversation_id=?", (conv_id,)
        )
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def test_retry_after_edit_uses_edited_text(monkeypatch):
    """The whole point of ✏️ + 🔄: edit a question, retry it, and the model is
    asked the NEW wording (context up to it, old answers excluded)."""
    headers = auth_headers()
    cid, q_id, _a_id = _make_dialog(headers)
    assert client.patch(
        f"/api/conversations/{cid}/messages/{q_id}", headers=headers,
        json={"content": "What is the capital of Spain?"},
    ).status_code == 200

    captured: dict = {}

    def fake_completion(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        captured["messages"] = messages
        return {"content": "Madrid."}

    from app.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "chat_completion_full", fake_completion)

    resp = client.post(
        "/api/chat/retry", headers=headers,
        json={"conversation_id": cid, "message_id": q_id, "models": ["m"]},
    )
    assert resp.status_code == 200, resp.text
    roles_contents = [(m["role"], m["content"]) for m in captured["messages"]]
    assert ("user", "What is the capital of Spain?") in roles_contents
    assert ("user", "What is 2+2?") not in roles_contents
    assert ("assistant", "4") not in roles_contents
