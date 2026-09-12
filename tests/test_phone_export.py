"""Tests for the Android-transfer endpoints:

- GET /api/export/phone — the whole account in the phone app's AsyncStorage
  format (openrouter.dialogs.v1 / openrouter.batches.history.v1), the mirror
  of POST /api/import/phone.
- The exported JSON must round-trip: re-importing it after deleting the
  dialogs recreates them with the same content (and batch results).
"""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def login() -> str:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {login()}"}


def _pair_headers(pair_code: str) -> dict:
    resp = client.post("/api/auth/pair", json={"code": pair_code})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _make_dialog_and_batch(headers: dict) -> tuple[str, str]:
    client.post(
        "/api/sync/push",
        headers=headers,
        json={
            "dialogs": [
                {
                    "id": "exp-dialog-1",
                    "title": "Exported chat",
                    "model": "test/model-a",
                    "messages": [
                        {"role": "user", "content": "question one"},
                        {
                            "role": "assistant",
                            "content": "answer one",
                            "model": "test/model-a",
                            "reasoning": "high",
                            "provider": "TestProvider",
                            "total_tokens": 12,
                            "cost": 0.0001,
                        },
                    ],
                }
            ],
            "batches": [
                {
                    "id": "exp-batch-1",
                    "title": "Exported batch",
                    "model": "test/model-b",
                    "prompts": ["prompt one", "prompt two"],
                    "batch": {
                        "results": [
                            {
                                "custom_id": "req-1",
                                "response": {
                                    "status_code": 200,
                                    "body": {"choices": [{"message": {"content": "answer one"}}]},
                                },
                            },
                            {"custom_id": "req-2", "ok": False, "status": 500},
                        ],
                    },
                }
            ],
            "deleted_external_ids": [],
        },
    )
    return "exp-dialog-1", "exp-batch-1"


def _export(headers: dict) -> dict:
    resp = client.get("/api/export/phone", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_export_requires_auth():
    assert client.get("/api/export/phone").status_code == 401


def test_export_produces_phone_format():
    headers = auth_headers()
    _make_dialog_and_batch(headers)

    data = _export(headers)
    assert set(data.keys()) == {"openrouter.dialogs.v1", "openrouter.batches.history.v1"}

    dialog = next(
        d for d in data["openrouter.dialogs.v1"] if d["id"] == "exp-dialog-1"
    )
    assert dialog["title"] == "Exported chat"
    assert dialog["model"] == "test/model-a"
    assert [m["content"] for m in dialog["messages"]] == ["question one", "answer one"]
    answer = dialog["messages"][1]
    assert answer["provider"] == "TestProvider"
    assert answer["total_tokens"] == 12
    assert isinstance(dialog["createdAt"], int)

    batch = next(
        b for b in data["openrouter.batches.history.v1"] if b["id"] == "exp-batch-1"
    )
    assert batch["prompts"] == ["prompt one", "prompt two"]
    # req-1 succeeded, req-2 failed — both results reconstructed by custom_id.
    results = {r["custom_id"]: r for r in batch["batch"]["results"]}
    assert (
        results["req-1"]["response"]["body"]["choices"][0]["message"]["content"]
        == "answer one"
    )
    assert "error" in results["req-2"]

    _hard_delete(["exp-dialog-1", "exp-batch-1"])


def test_export_round_trips_through_import():
    headers = auth_headers()
    _make_dialog_and_batch(headers)
    data = _export(headers)
    _hard_delete(["exp-dialog-1", "exp-batch-1"])

    # Feed the export straight back through the phone-import endpoint
    # (trimmed to the two items this test created).
    trimmed = {
        "openrouter.dialogs.v1": [
            d for d in data["openrouter.dialogs.v1"] if d["id"] == "exp-dialog-1"
        ],
        "openrouter.batches.history.v1": [
            b for b in data["openrouter.batches.history.v1"] if b["id"] == "exp-batch-1"
        ],
    }
    resp = client.post("/api/import/phone", headers=headers, json=trimmed)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["conversations_created"] == 2

    convs = client.get("/api/conversations", headers=headers).json()
    dialog = next(c for c in convs if c["title"] == "Exported chat")
    batch = next(c for c in convs if c["title"] == "Exported batch")
    assert dialog["kind"] == "chat" and batch["kind"] == "batch"

    dlg_detail = client.get(f"/api/conversations/{dialog['id']}", headers=headers).json()
    assert [m["content"] for m in dlg_detail["messages"]] == ["question one", "answer one"]

    batch_detail = client.get(f"/api/conversations/{batch['id']}", headers=headers).json()
    assert [m["content"] for m in batch_detail["messages"]] == [
        "prompt one", "answer one", "prompt two", "[error] HTTP 500",
    ]

    _hard_delete(["exp-dialog-1", "exp-batch-1"])


def _hard_delete(external_ids: list[str]) -> None:
    """Test-only hard delete: removes rows AND tombstones, so a later test can
    push the same external_ids again (import skips ids that still exist)."""
    import sqlite3

    from app.database import engine

    with sqlite3.connect(engine.url.database) as conn:
        for ext in external_ids:
            row = conn.execute(
                "SELECT id FROM conversations WHERE external_id=?", (ext,)
            ).fetchone()
            if not row:
                continue
            conv_id = row[0]
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
            conn.execute(
                "DELETE FROM message_tombstones WHERE conversation_id=?", (conv_id,)
            )
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def test_export_isolated_per_account():
    """A client account's dialogs never leak into another account's export."""
    owner = auth_headers()
    created = client.post(
        "/api/auth/accounts",
        json={"admin_password": "test", "label": "expiso", "client_password": "exp-pw-1"},
    )
    assert created.status_code == 201, created.text
    other = _pair_headers(created.json()["pair_code"])

    _make_dialog_and_batch(owner)
    client.post(
        "/api/sync/push",
        headers=other,
        json={
            "dialogs": [
                {
                    "id": "other-account-dialog",
                    "title": "Private",
                    "messages": [{"role": "user", "content": "mine"}],
                }
            ],
            "batches": [],
            "deleted_external_ids": [],
        },
    )

    owner_ids = {d["id"] for d in _export(owner)["openrouter.dialogs.v1"]}
    other_ids = {d["id"] for d in _export(other)["openrouter.dialogs.v1"]}
    assert "exp-dialog-1" in owner_ids
    assert "other-account-dialog" not in owner_ids
    assert "other-account-dialog" in other_ids
    assert "exp-dialog-1" not in other_ids

    _hard_delete(["exp-dialog-1", "exp-batch-1"])
    _hard_delete(["other-account-dialog"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL_TESTS_PASSED")
