"""Tests for GET /api/chat/models/all — the picker's full provider catalog
(search + the sorted all-models list)."""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

FAKE_CATALOG = [
    {
        "id": "old/cheap-model",
        "name": "Old: Cheap Model",
        "created": 1_000_000_000,
        "context_length": 8192,
        "prompt": 1e-07,
        "completion": 2e-07,
    },
    {
        "id": "new/pricey-model",
        "name": "New: Pricey Model",
        "created": 2_000_000_000,
        "context_length": 200000,
        "prompt": 2e-06,
        "completion": 8e-06,
    },
]


def auth_headers() -> dict:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_models_all_requires_auth():
    assert client.get("/api/chat/models/all").status_code == 401


def test_models_all_returns_full_catalog(monkeypatch):
    import app.services.openrouter as openrouter

    monkeypatch.setattr(openrouter, "fetch_model_catalog", lambda: FAKE_CATALOG)

    resp = client.get("/api/chat/models/all", headers=auth_headers())
    assert resp.status_code == 200, resp.text
    models = resp.json()["models"]
    assert {m["id"] for m in models} == {"old/cheap-model", "new/pricey-model"}
    entry = next(m for m in models if m["id"] == "new/pricey-model")
    assert entry["name"] == "New: Pricey Model"
    assert entry["created"] == 2_000_000_000
    assert entry["completion"] == 8e-06


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL_TESTS_PASSED")
