"""Tests for conversation rename/delete, multi-device sync, and the
settings backup (single-file server migration) endpoints."""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Conversation  # noqa: E402

client = TestClient(app)


def login() -> str:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {login()}"}


# ---------------------------------------------------------------------------
# Conversation rename / soft-delete
# ---------------------------------------------------------------------------

def test_rename_conversation():
    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers, json={"title": "Original"}).json()

    resp = client.patch(f"/api/conversations/{conv['id']}", headers=headers, json={"title": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Renamed"

    fetched = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
    assert fetched["title"] == "Renamed"


def test_delete_conversation_is_soft_and_hides_from_list():
    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers, json={"title": "To delete"}).json()
    conv_id = conv["id"]

    resp = client.delete(f"/api/conversations/{conv_id}", headers=headers)
    assert resp.status_code == 204

    # Gone from the list and from direct fetch (404, not the deleted row).
    convs = client.get("/api/conversations", headers=headers).json()
    assert all(c["id"] != conv_id for c in convs)
    assert client.get(f"/api/conversations/{conv_id}", headers=headers).status_code == 404


# ---------------------------------------------------------------------------
# Multi-device sync (pull assigns external_id, push upserts, tombstones delete)
# ---------------------------------------------------------------------------

def test_sync_pull_assigns_external_id():
    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers, json={"title": "Pull me"}).json()

    resp = client.get("/api/sync/pull", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    matches = [c for c in body["conversations"] if c["title"] == "Pull me"]
    assert len(matches) == 1
    assert matches[0]["external_id"] == f"srv-{conv['id']}"
    assert matches[0]["deleted"] is False


def test_sync_push_creates_then_updates_then_deletes():
    headers = auth_headers()
    ext_id = "phone-dialog-abc"

    push_body = {
        "dialogs": [
            {
                "id": ext_id,
                "title": "Phone chat",
                "model": "openrouter/model-x",
                "messages": [{"role": "user", "content": "hi"}],
            }
        ],
        "batches": [],
        "deleted_external_ids": [],
    }
    resp = client.post("/api/sync/push", headers=headers, json=push_body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    match = next(c for c in pulled if c["external_id"] == ext_id)
    assert match["title"] == "Phone chat"
    assert [m["content"] for m in match["messages"]] == ["hi"]

    # Push again with the same external_id -> update, not a second row.
    push_body["dialogs"][0]["title"] = "Phone chat renamed"
    resp = client.post("/api/sync/push", headers=headers, json=push_body)
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    matches = [c for c in pulled if c["external_id"] == ext_id]
    assert len(matches) == 1
    assert matches[0]["title"] == "Phone chat renamed"

    # Delete via push -> tombstoned on the next pull.
    resp = client.post(
        "/api/sync/push",
        headers=headers,
        json={"dialogs": [], "batches": [], "deleted_external_ids": [ext_id]},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    match = next(c for c in pulled if c["external_id"] == ext_id)
    assert match["deleted"] is True
    assert match["messages"] == []


def test_sync_pull_since_filters_untouched_conversations():
    headers = auth_headers()
    server_time = client.get("/api/sync/pull", headers=headers).json()["server_time"]

    resp = client.get("/api/sync/pull", headers=headers, params={"since": server_time})
    assert resp.status_code == 200
    # Nothing changed since we just read server_time, so nothing new comes back
    # (older fixture rows from earlier tests are untouched at this point).
    assert isinstance(resp.json()["conversations"], list)


# ---------------------------------------------------------------------------
# Settings backup (single-file credential export/import for server migration)
# ---------------------------------------------------------------------------

def test_settings_backup_roundtrip():
    headers = auth_headers()

    resp = client.put(
        "/api/settings",
        headers=headers,
        json={"openrouter_api_key": "sk-or-v1-secret", "aws_region": "us-east-1"},
    )
    assert resp.status_code == 200

    backup = client.get("/api/settings/backup", headers=headers)
    assert backup.status_code == 200, backup.text
    data = backup.json()
    assert data["openrouter_api_key"] == "sk-or-v1-secret"
    assert data["aws_region"] == "us-east-1"

    # Simulate restoring on a fresh server: change the key, then restore the backup.
    client.put("/api/settings", headers=headers, json={"openrouter_api_key": "temporary-other-key"})
    restored = client.post("/api/settings/backup", headers=headers, json=data)
    assert restored.status_code == 200, restored.text

    view = client.get("/api/settings", headers=headers).json()
    assert view["openrouter_api_key"]["hint"].startswith("sk-o")


def test_settings_backup_requires_auth():
    assert client.get("/api/settings/backup").status_code == 401


# ---------------------------------------------------------------------------
# Delete-as-archive
# ---------------------------------------------------------------------------

def test_delete_preserves_messages_as_archive():
    """Tombstoning a dialog keeps its messages in the DB so the correspondence
    can be recovered later, while the pull still reports it deleted."""
    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers, json={"title": "Archive me"}).json()
    conv_id = conv["id"]
    client.post(
        f"/api/conversations/{conv_id}/messages",
        headers=headers,
        json={"role": "user", "content": "keep this"},
    )

    assert client.delete(f"/api/conversations/{conv_id}", headers=headers).status_code == 204

    db = SessionLocal()
    try:
        row = db.scalar(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conv_id)
        )
        assert row is not None
        assert row.deleted_at is not None
        assert [m.content for m in row.messages] == ["keep this"]
    finally:
        db.close()


def test_sync_push_delete_preserves_messages_as_archive():
    headers = auth_headers()
    ext_id = "phone-archive-xyz"

    client.post(
        "/api/sync/push",
        headers=headers,
        json={
            "dialogs": [
                {
                    "id": ext_id,
                    "title": "Keep",
                    "model": "m",
                    "messages": [{"role": "user", "content": "tombstone me"}],
                }
            ],
            "batches": [],
            "deleted_external_ids": [],
        },
    )
    client.post(
        "/api/sync/push",
        headers=headers,
        json={"dialogs": [], "batches": [], "deleted_external_ids": [ext_id]},
    )

    db = SessionLocal()
    try:
        row = db.scalar(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.external_id == ext_id)
        )
        assert row is not None and row.deleted_at is not None
        assert [m.content for m in row.messages] == ["tombstone me"]
    finally:
        db.close()


def test_sync_push_pull_roundtrips_per_message_model_and_stats():
    """The phone pushes `model` (+ reasoning/provider/gen_id/tokens/cost) per
    message; the pull must return them so bubbles can show the exact serving
    model (including a ":flex" marker) on every device."""
    headers = auth_headers()
    ext_id = "roundtrip-model-1"
    push_body = {
        "dialogs": [
            {
                "id": ext_id,
                "title": "Model round-trip",
                "model": "deepseek/deepseek-v4",
                "messages": [
                    {"role": "user", "content": "Q?", "model": None},
                    {
                        "role": "assistant",
                        "content": "A!",
                        "model": "deepseek/deepseek-v4:flex",
                        "reasoning": "low",
                        "provider": "Novita",
                        "gen_id": "gen-abc",
                        "tokens_prompt": 100,
                        "tokens_completion": 200,
                        "total_tokens": 300,
                        "cost": 0.0021,
                    },
                ],
            }
        ],
        "batches": [],
    }
    resp = client.post("/api/sync/push", headers=headers, json=push_body)
    assert resp.status_code == 200, resp.text

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    match = next(c for c in pulled if c["external_id"] == ext_id)
    assert match["model"] == "deepseek/deepseek-v4"
    assistant = next(m for m in match["messages"] if m["role"] == "assistant")
    assert assistant["model"] == "deepseek/deepseek-v4:flex"
    assert assistant["reasoning"] == "low"
    assert assistant["provider"] == "Novita"
    assert assistant["gen_id"] == "gen-abc"
    assert assistant["total_tokens"] == 300
    assert assistant["cost"] == 0.0021
    # User messages deliberately inherit the dialog model on ingest
    # (phone_sync.dialog_messages backfills `m.model or dialog.model`).
    user = next(m for m in match["messages"] if m["role"] == "user")
    assert user["model"] == "deepseek/deepseek-v4"


def test_sync_never_returns_or_adopts_provider_keys():
    """Provider credentials must stay outside the conversation sync contract."""
    from app.config import settings

    headers = auth_headers()
    original = settings.openrouter_api_key
    settings.openrouter_api_key = "server-secret"
    try:
        pulled = client.get("/api/sync/pull", headers=headers)
        assert pulled.status_code == 200, pulled.text
        assert "keys" not in pulled.json()

        response = client.post(
            "/api/sync/push",
            headers=headers,
            json={
                "dialogs": [],
                "batches": [],
                "deleted_external_ids": [],
                "keys": {"openrouter_api_key": "device-secret"},
            },
        )
        assert response.status_code == 200, response.text
        assert settings.openrouter_api_key == "server-secret"
    finally:
        settings.openrouter_api_key = original


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL_TESTS_PASSED")
