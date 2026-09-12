"""Tests for the generic OpenAI-compatible "custom:" provider.

Covers the provider client itself (real HTTP against a tiny local mock that
speaks the OpenAI shapes) and the dispatch through app.services.providers.
The key-status check for custom_api_key is exercised through the /api/settings
endpoint, mirroring how the web UI saves the configuration.

The mock also proves the failure path: a bad model returns an OpenAI-style
error body and the provider must surface its human-readable message.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import custom_provider, providers  # noqa: E402
from app.services.provider_errors import ProviderError  # noqa: E402

client = TestClient(app)

MOCK_PORT = 8895
MOCK_BASE = f"http://127.0.0.1:{MOCK_PORT}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if self.path == "/models":
            if auth in ("", "Bearer sk-custom-good"):
                self._send(200, {"data": [{"id": "m-1"}, {"id": "m-2"}]})
            else:
                self._send(401, {"error": {"message": "Bad key"}})
        else:
            self._send(404, {"error": {"message": "No such path"}})

    def do_POST(self):
        if self.path == "/chat/completions":
            body = self._read_body()
            if body.get("model") == "fail-model":
                self._send(400, {
                    "error": {"message": "Model 'fail-model' does not exist"},
                })
                return
            self._send(200, {
                "id": "gen-xyz",
                "provider": "MockGateway",
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "prompt_tokens_details": {"cached_tokens": 4},
                },
            })
        else:
            self._send(404, {"error": {"message": "No such path"}})

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        import json
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def _send(self, code: int, obj: dict):
        import json
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


MOCK = HTTPServer(("127.0.0.1", MOCK_PORT), Handler)
threading.Thread(target=MOCK.serve_forever, daemon=True).start()


@pytest.fixture()
def custom_cfg(monkeypatch):
    """Point the custom provider at the mock and restore the keys after."""
    saved = (settings.custom_base_url, settings.custom_api_key,
             settings.custom_default_model)
    monkeypatch.setattr(settings, "custom_base_url", MOCK_BASE)
    monkeypatch.setattr(settings, "custom_api_key", "")
    monkeypatch.setattr(settings, "custom_default_model", "")
    yield
    settings.custom_base_url, settings.custom_api_key, \
        settings.custom_default_model = saved


def login() -> str:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {login()}"}


# ---------------------------------------------------------------------------
# Provider client
# ---------------------------------------------------------------------------

def test_keyless_custom_completion(custom_cfg):
    # No Authorization header must be sent when no key is set (local Ollama).
    assert custom_provider.chat_completion("mock-model", [
        {"role": "user", "content": "ping"},
    ]) == "pong"


def test_keyed_custom_completion_sends_bearer(custom_cfg, monkeypatch):
    monkeypatch.setattr(settings, "custom_api_key", "sk-custom-good")
    full = custom_provider.chat_completion_full(
        "mock-model", [{"role": "user", "content": "ping"}], temperature=0.5,
    )
    assert full["content"] == "pong"
    assert full["provider"] == "MockGateway"
    assert full["gen_id"] == "gen-xyz"
    assert full["tokens_prompt"] == 10
    assert full["tokens_cached"] == 4
    assert full["tokens_completion"] == 2
    assert full["total_tokens"] == 12


def test_custom_error_surfaces_provider_message(custom_cfg):
    # The OpenAI-shaped error body's message must reach the user verbatim.
    with pytest.raises(ProviderError) as exc_info:
        custom_provider.chat_completion_full(
            "fail-model", [{"role": "user", "content": "ping"}],
        )
    assert "Model 'fail-model' does not exist" in str(exc_info.value)


def test_custom_provider_requires_base_url(monkeypatch):
    monkeypatch.setattr(settings, "custom_base_url", "")
    with pytest.raises(ProviderError) as exc_info:
        custom_provider.chat_completion("m", [{"role": "user", "content": "x"}])
    assert "not configured" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Dispatch (model prefix routing)
# ---------------------------------------------------------------------------

def test_dispatch_routes_custom_prefix(custom_cfg):
    assert providers.chat_completion("custom:mock-model", [
        {"role": "user", "content": "ping"},
    ]) == "pong"
    full = providers.chat_completion_full(
        "custom:mock-model", [{"role": "user", "content": "ping"}],
    )
    assert full["content"] == "pong"
    assert full["provider"] == "MockGateway"


def test_custom_model_joins_defaults_when_configured(custom_cfg):
    defaults = providers.default_models()
    assert "custom:mock-model" not in defaults  # custom_default_model unset
    assert providers.configured_status()["custom_configured"] is True
    assert "custom:default" in defaults

    settings.custom_default_model = "mock-model"
    defaults = providers.default_models()
    assert defaults[0] == "custom:mock-model"


# ---------------------------------------------------------------------------
# Settings wiring: save via the API → key check runs against the mock
# ---------------------------------------------------------------------------

def test_settings_roundtrip_custom_fields(custom_cfg):
    headers = auth_headers()
    resp = client.put("/api/settings", headers=headers, json={
        "custom_api_key": "sk-custom-good",
        "custom_base_url": MOCK_BASE,
        "custom_default_model": "mock-model",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["custom_base_url"]["value"] == MOCK_BASE
    assert body["custom_default_model"]["value"] == "mock-model"
    assert body["checked_keys"]["custom_api_key"]["status"] == "valid"

    # GET returns the saved values (key masked) + the stored status verdict.
    view = client.get("/api/settings", headers=headers).json()
    assert view["custom_api_key"]["configured"] is True
    assert view["custom_api_key"]["hint"].startswith("sk-c")
    assert view["custom_api_key"]["status"]["status"] == "valid"
    assert view["custom_base_url"]["value"] == MOCK_BASE
    assert view["custom_default_model"]["value"] == "mock-model"

    # The singleton picked everything up immediately, no restart needed.
    assert settings.custom_base_url == MOCK_BASE
    assert settings.custom_api_key == "sk-custom-good"
    assert settings.custom_default_model == "mock-model"
    assert custom_provider.is_configured() is True


def test_settings_save_normalizes_trailing_slash(custom_cfg):
    headers = auth_headers()
    resp = client.put("/api/settings", headers=headers, json={
        "custom_base_url": MOCK_BASE + "/",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["custom_base_url"]["value"] == MOCK_BASE
    assert settings.custom_base_url == MOCK_BASE


def test_custom_key_delete_route(custom_cfg):
    headers = auth_headers()
    resp = client.put(
        "/api/settings", headers=headers, json={"custom_api_key": "sk-custom-good"},
    )
    assert resp.status_code == 200
    resp = client.delete("/api/settings/keys/custom_api_key", headers=headers)
    assert resp.status_code == 200, resp.text
    assert settings.custom_api_key == ""
    view = client.get("/api/settings", headers=headers).json()
    assert view["custom_api_key"]["configured"] is False


def test_backup_includes_custom_fields(custom_cfg):
    headers = auth_headers()
    client.put("/api/settings", headers=headers, json={
        "custom_api_key": "sk-custom-good",
        "custom_base_url": MOCK_BASE,
    })
    backup = client.get("/api/settings/backup", headers=headers).json()
    assert backup["custom_api_key"] == "sk-custom-good"
    assert backup["custom_base_url"] == MOCK_BASE
