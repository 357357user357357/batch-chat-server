"""Tests for GET /api/chat/models/all — the picker's full provider catalog
(search + the sorted all-models list), including the custom provider's
gateway catalog merged in with "custom:"-prefixed ids."""

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
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

# FastRouter-style gateway: OpenRouter-schema entries with STRING pricing
# (blank for non-token costs) plus one bare id-only entry, like LM Studio
# and friends return.
GATEWAY_CATALOG = {
    "data": [
        {
            "id": "z-ai/glm-5.3-flash",
            "name": "GLM 5.3 Flash",
            "created": 1_787_752_741,
            "context_length": 1_310_720,
            "pricing": {
                "prompt": "0.000000075",
                "completion": "0.00000025",
                "request": "",
                "image": "",
            },
        },
        {"id": "bare-local-model"},
    ]
}


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        import json
        if self.path == "/models":
            data = json.dumps(GATEWAY_CATALOG).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture()
def gateway(monkeypatch):
    """A local mock gateway + the custom provider pointed at it."""
    server = HTTPServer(("127.0.0.1", 0), GatewayHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    import app.services.custom_provider as custom_provider
    monkeypatch.setattr(settings, "custom_base_url", base_url)
    monkeypatch.setattr(settings, "custom_api_key", "sk-test")
    monkeypatch.setattr(custom_provider, "_CATALOG_CACHE",
                        {"data": None, "ts": 0.0})
    yield
    server.shutdown()


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


def test_models_all_appends_custom_catalog(gateway, monkeypatch):
    import app.services.openrouter as openrouter

    monkeypatch.setattr(openrouter, "fetch_model_catalog", lambda: FAKE_CATALOG)

    models = client.get(
        "/api/chat/models/all", headers=auth_headers(),
    ).json()["models"]
    ids = {m["id"] for m in models}
    assert {"old/cheap-model", "new/pricey-model"} <= ids
    # Gateway entries arrive with the "custom:" prefix, ready to dispatch.
    glm = next(m for m in models if m["id"] == "custom:z-ai/glm-5.3-flash")
    assert glm["name"] == "GLM 5.3 Flash"
    assert glm["created"] == 1_787_752_741
    assert glm["context_length"] == 1_310_720
    # String prices from the OpenRouter-style schema are mapped to floats.
    assert glm["prompt"] == 7.5e-08
    assert glm["completion"] == 2.5e-07
    # Bare id-only entries still show up (price 0, id as name).
    bare = next(m for m in models if m["id"] == "custom:bare-local-model")
    assert bare["name"] == "bare-local-model"
    assert bare["prompt"] == 0.0 and bare["completion"] == 0.0


def test_models_all_without_custom_configured_is_openrouter_only(monkeypatch):
    import app.services.custom_provider as custom_provider
    import app.services.openrouter as openrouter

    monkeypatch.setattr(openrouter, "fetch_model_catalog", lambda: FAKE_CATALOG)
    monkeypatch.setattr(settings, "custom_base_url", "")
    monkeypatch.setattr(custom_provider, "_CATALOG_CACHE",
                        {"data": None, "ts": 0.0})

    models = client.get(
        "/api/chat/models/all", headers=auth_headers(),
    ).json()["models"]
    assert {m["id"] for m in models} == {"old/cheap-model", "new/pricey-model"}


def test_models_all_survives_gateway_failure(gateway, monkeypatch):
    import app.services.openrouter as openrouter

    monkeypatch.setattr(openrouter, "fetch_model_catalog", lambda: FAKE_CATALOG)
    # Gateway dies after the fixture is set up → endpoint must still answer
    # with the OpenRouter catalog, never a 500.
    monkeypatch.setattr(settings, "custom_base_url",
                        "http://127.0.0.1:1/nope")

    models = client.get(
        "/api/chat/models/all", headers=auth_headers(),
    ).json()["models"]
    assert {m["id"] for m in models} == {"old/cheap-model", "new/pricey-model"}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL_TESTS_PASSED")
