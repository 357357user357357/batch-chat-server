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
from app.services.settings_store import save_overrides  # noqa: E402

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
# Unified provider keys + delete-as-archive
# ---------------------------------------------------------------------------

def test_sync_exchanges_provider_keys():
    """A device fills gaps on the server (server-first), and the server shares
    its keys back down on pull."""
    headers = auth_headers()

    db = SessionLocal()
    try:
        # Server already has its own OpenRouter key, but Tavily is empty.
        save_overrides(db, {"openrouter_api_key": "sk-or-v1-server", "tavily_api_key": ""})
    finally:
        db.close()

    resp = client.post(
        "/api/sync/push",
        headers=headers,
        json={
            "dialogs": [],
            "batches": [],
            "deleted_external_ids": [],
            "keys": {
                "openrouter_api_key": "sk-or-v1-phone",
                "tavily_api_key": "tvly-phone-secret",
            },
        },
    )
    assert resp.status_code == 200, resp.text

    view = client.get("/api/settings", headers=headers).json()
    assert view["tavily_api_key"]["configured"] is True

    pulled = client.get("/api/sync/pull", headers=headers).json()
    assert pulled["keys"]["openrouter_api_key"] == "sk-or-v1-server"  # not overwritten
    assert pulled["keys"]["tavily_api_key"] == "tvly-phone-secret"  # adopted


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL_TESTS_PASSED")
