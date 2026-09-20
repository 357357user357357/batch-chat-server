"""Tests for the web UI API-key management (status check + delete) and the
prompt-cache usage endpoint + tokens_cached capture.

Runs against the shared app/mock-OpenRouter setup from conftest +
test_jsonl_batches; the provider status checks point at a tiny local mock
(monkeypatched base URL / usage URL) so no real provider is ever contacted.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AppSetting  # noqa: E402
from app.services.openrouter import chat_completion_full  # noqa: E402

client = TestClient(app)


def sse_data(resp) -> dict:
    """Final `data:` JSON event of an SSE chat response (/api/chat/send)."""
    events = [
        line[len("data: "):]
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events, f"no SSE data event in: {resp.text[:400]}"
    return json.loads(events[-1])

MOCK_PORT = 8893
MOCK_BASE = f"http://127.0.0.1:{MOCK_PORT}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if self.path == "/key":
            if auth == "Bearer sk-or-good":
                self._send(200, {"data": {"label": "Main key", "is_free_tier": False}})
            else:
                self._send(401, {"error": "Invalid key"})
        elif self.path == "/usage":
            if auth == "Bearer tvly-good":
                self._send(200, {
                    "key": {"usage": 5, "limit": 1000},
                    "account": {"current_plan": "basic"},
                })
            else:
                self._send(401, {"detail": "Invalid API key"})
        else:
            self._send(404, {})

    def do_POST(self):
        body = self._read_body()
        if body.get("stream"):
            # Chat answers stream (SSE): keep-alive comment, content delta,
            # final usage chunk, [DONE].
            self._send_sse(
                200,
                content="hi there",
                gen_id="gen-mock",
                provider="Mock",
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                    "cost": 0.0007,
                    "prompt_tokens_details": {"cached_tokens": 60},
                },
            )
            return
        self._send(200, {
            "choices": [{"message": {"role": "assistant", "content": "hi there"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cost": 0.0007,
                "prompt_tokens_details": {"cached_tokens": 60},
            },
        })

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return __import__("json").loads(raw)
        except ValueError:
            return {}

    def _send_sse(self, code: int, content: str, gen_id: str,
                  provider: str, usage: dict):
        import json as _json

        def event(obj) -> bytes:
            return (_json.dumps(obj) + "\n\n").encode()

        payload = b"".join([
            b": OPENROUTER PROCESSING\n\n",
            event({"id": gen_id, "provider": provider,
                   "choices": [{"delta": {"content": content}}]}),
            event({"id": gen_id, "provider": provider,
                   "choices": [{"delta": {}, "finish_reason": "stop"}],
                   "usage": usage}),
            b"data: [DONE]\n\n",
        ])
        self.send_response(code)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send(self, code: int, obj: dict):
        data = __import__("json").dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


MOCK = HTTPServer(("127.0.0.1", MOCK_PORT), Handler)
threading.Thread(target=MOCK.serve_forever, daemon=True).start()


def login() -> str:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {login()}"}


@pytest.fixture()
def saved_keys(monkeypatch):
    """Point the providers at the mock and restore the singleton keys after."""
    monkeypatch.setattr(settings, "openrouter_base_url", MOCK_BASE)
    from app.services import tavily as tavily_mod

    monkeypatch.setattr(tavily_mod, "TAVILY_USAGE_URL", f"{MOCK_BASE}/usage")
    saved_openrouter = settings.openrouter_api_key
    saved_tavily = settings.tavily_api_key
    yield
    settings.openrouter_api_key = saved_openrouter
    settings.tavily_api_key = saved_tavily


# ---------------------------------------------------------------------------
# Key status: auto-check on paste, manual check, invalid, delete
# ---------------------------------------------------------------------------

def test_pasted_openrouter_key_is_checked_and_status_shown(saved_keys):
    headers = auth_headers()
    resp = client.put(
        "/api/settings",
        headers=headers,
        json={"openrouter_api_key": "sk-or-good"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    verdict = body["checked_keys"]["openrouter_api_key"]
    assert verdict["status"] == "valid"
    assert verdict["detail"] == "Main key"

    # The verdict is persisted and comes back on every GET (masked key).
    view = client.get("/api/settings", headers=headers).json()
    assert view["openrouter_api_key"]["configured"] is True
    assert view["openrouter_api_key"]["hint"].startswith("sk-o")
    assert view["openrouter_api_key"]["status"]["status"] == "valid"
    assert view["openrouter_api_key"]["status"]["detail"] == "Main key"


def test_invalid_openrouter_key_reports_rejected(saved_keys):
    headers = auth_headers()
    resp = client.put(
        "/api/settings", headers=headers, json={"openrouter_api_key": "sk-or-bad"}
    )
    assert resp.status_code == 200
    assert resp.json()["checked_keys"]["openrouter_api_key"]["status"] == "invalid"
    assert "Rejected" in resp.json()["checked_keys"]["openrouter_api_key"]["detail"]


def test_manual_check_endpoint(saved_keys):
    headers = auth_headers()
    client.put("/api/settings", headers=headers, json={"openrouter_api_key": "sk-or-good"})
    resp = client.post("/api/settings/keys/openrouter_api_key/check", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "valid"

    with SessionLocal() as db:
        row = db.get(AppSetting, "openrouter_api_key.status")
        assert row is not None and "valid" in row.value

def test_tavily_key_check_with_plan_info(saved_keys):
    headers = auth_headers()
    resp = client.put("/api/settings", headers=headers, json={"tavily_api_key": "tvly-good"})
    assert resp.status_code == 200
    verdict = resp.json()["checked_keys"]["tavily_api_key"]
    assert verdict["status"] == "valid"
    assert verdict["detail"] == "5/1000 credits used"
    assert verdict["info"]["plan"] == "basic"

    view = client.get("/api/settings", headers=headers).json()
    assert view["tavily_api_key"]["status"]["status"] == "valid"


def test_delete_key_clears_value_and_status(saved_keys):
    headers = auth_headers()
    client.put("/api/settings", headers=headers, json={"openrouter_api_key": "sk-or-good"})
    resp = client.delete("/api/settings/keys/openrouter_api_key", headers=headers)
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["openrouter_api_key"]["configured"] is False
    assert view["openrouter_api_key"]["status"] is None
    assert settings.openrouter_api_key == ""


def test_unknown_field_has_no_check(saved_keys):
    headers = auth_headers()
    resp = client.post("/api/settings/keys/google_service_account_json/check", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Prompt-cache capture (tokens_cached) in the OpenRouter client and chat flow
# ---------------------------------------------------------------------------

def test_chat_completion_full_extracts_cached_tokens(monkeypatch):
    class FakeResponse:
        status_code = 200

        def __init__(self):
            self._sse = [
                'data: {"id": "gen-1", "provider": "Mock", '
                '"choices": [{"delta": {"content": "ok"}}]}',
                'data: {"choices": [{"delta": {}, "finish_reason": "stop"}],'
                ' "usage": {"prompt_tokens": 500, "completion_tokens": 20,'
                ' "total_tokens": 520, "cost": 0.001,'
                ' "prompt_tokens_details": {"cached_tokens": 350}}}',
                "data: [DONE]",
            ]

        def read(self):
            return b""

        def iter_lines(self):
            return iter(self._sse)

    class FakeStream:
        def __init__(self, response):
            self._response = response

        def __enter__(self):
            return self._response

        def __exit__(self, *a):
            return False

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, headers=None, json=None):
            return FakeStream(FakeResponse())

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    info = chat_completion_full("test/model", [{"role": "user", "content": "hi"}])
    assert info["tokens_cached"] == 350
    assert info["tokens_prompt"] == 500


def test_chat_send_persists_and_reports_cached_tokens(monkeypatch):
    from app.routers import chat as chat_router

    def fake_full(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        return {
            "content": "cached answer",
            "provider": "Mock",
            "gen_id": "gen-cache-1",
            "tokens_prompt": 500,
            "tokens_cached": 350,
            "tokens_completion": 20,
            "total_tokens": 520,
            "cost": 0.001,
        }

    monkeypatch.setattr(chat_router, "chat_completion_full", fake_full)

    headers = auth_headers()
    resp = client.post(
        "/api/chat/send",
        headers=headers,
        json={"user_message": "cache me", "models": ["test/model"]},
    )
    assert resp.status_code == 200, resp.text
    data = sse_data(resp)
    item = data["responses"][0]
    assert item["tokens_cached"] == 350

    detail = client.get(f"/api/conversations/{data['conversation_id']}", headers=headers).json()
    stored = next(m for m in detail["messages"] if m["content"] == "cached answer")
    assert stored["tokens_cached"] == 350


def test_sync_pull_carries_cached_tokens(monkeypatch):
    """A message with cached tokens shows them in a phone sync pull too."""
    from app.routers import chat as chat_router

    monkeypatch.setattr(
        chat_router,
        "chat_completion_full",
        lambda model, messages, temperature=None, max_tokens=None, reasoning_effort=None: {
            "content": "sync cached",
            "tokens_prompt": 300,
            "tokens_cached": 120,
            "tokens_completion": 5,
        },
    )
    headers = auth_headers()
    resp = client.post(
        "/api/chat/send", headers=headers,
        json={"user_message": "sync me", "models": ["test/model"]},
    )
    cid = sse_data(resp)["conversation_id"]
    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    dlg = next(c for c in pulled if c["external_id"] == f"srv-{cid}")
    msg = next(m for m in dlg["messages"] if m["content"] == "sync cached")
    assert msg["tokens_cached"] == 120


# ---------------------------------------------------------------------------
# /api/stats/prompt-cache (daily cached vs uncached buckets)
# ---------------------------------------------------------------------------

def test_prompt_cache_stats_buckets_and_totals():
    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers, json={"title": "usage"}).json()

    from datetime import datetime, timedelta

    from app.models import Message
    from app.models import utcnow

    today = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    rows = [
        # (days_ago, tokens_prompt, tokens_cached)
        (0, 1000, 400),   # today: 40% cached
        (0, 500, None),   # today: old row without cache info -> uncached
        (2, 2000, 1500),
        (10, 300, 0),
    ]
    with SessionLocal() as db:
        for days_ago, prompt, cached in rows:
            db.add(Message(
                conversation_id=conv["id"],
                role="assistant",
                content=f"u{days_ago}-{prompt}-{cached}",
                model="m",
                tokens_prompt=prompt,
                tokens_cached=cached,
                created_at=today - timedelta(days=days_ago),
            ))
        db.commit()

    resp = client.get("/api/stats/prompt-cache?days=30", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["buckets"]) == 30  # full day range, no gaps

    totals = body["totals"]
    assert totals["prompt"] >= 3800
    assert totals["cached"] >= 1550
    assert totals["uncached"] == totals["prompt"] - totals["cached"]
    assert 0 < totals["cached_share"] <= 100

    today_bucket = next(b for b in body["buckets"] if b["date"] == today.date().isoformat())
    assert today_bucket["cached"] >= 400
    assert today_bucket["uncached"] >= 1100  # 600 from the first + 500 uncached row

    # A mid-range day with no messages must still exist with zeros.
    empty_day = (utcnow() - timedelta(days=5)).date().isoformat()
    empty = next(b for b in body["buckets"] if b["date"] == empty_day)
    assert empty["cached"] == 0 and empty["uncached"] == 0

    # Cleanup so other modules' counts are unaffected.
    import sqlite3

    from app.database import engine

    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv["id"],))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv["id"],))


# ---------------------------------------------------------------------------
# Client accounts manage the two shared provider keys (and nothing else)
# ---------------------------------------------------------------------------

def _client_headers(label: str) -> dict:
    created = client.post(
        "/api/auth/accounts",
        json={"admin_password": "test", "label": label, "client_password": "pw12345"},
    )
    assert created.status_code == 201, created.text
    login_resp = client.post(
        "/api/auth/login",
        json={"login": created.json()["account_id"], "password": "pw12345"},
    )
    assert login_resp.status_code == 200, login_resp.text
    return {"Authorization": f"Bearer {login_resp.json()['token']}"}


def test_client_sees_shared_keys_masked_and_can_replace_them(saved_keys):
    headers = auth_headers()
    client.put("/api/settings", headers=headers, json={"openrouter_api_key": "sk-or-good"})

    sub = _client_headers("key-client")

    # Client view: only the two provider keys, masked (enough head to tell
    # keys apart) and with the stored provider-check status.
    view = client.get("/api/settings", headers=sub).json()
    assert set(view) == {"openrouter_api_key", "custom_api_key", "tavily_api_key"}
    assert view["openrouter_api_key"]["configured"] is True
    assert view["openrouter_api_key"]["hint"].startswith("sk-o")
    assert "…" in view["openrouter_api_key"]["hint"]
    assert view["openrouter_api_key"]["status"]["status"] == "valid"

    # Client replaces the key: paste + save is provider-checked like for the
    # owner, and the response stays client-scoped.
    resp = client.put(
        "/api/settings", headers=sub, json={"openrouter_api_key": "sk-or-bad"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checked_keys"]["openrouter_api_key"]["status"] == "invalid"
    assert set(body) == {"openrouter_api_key", "custom_api_key", "tavily_api_key", "checked_keys"}

    # Everything beyond the two keys stays owner-only.
    assert client.put(
        "/api/settings", headers=sub, json={"cache_duration_seconds": 3600},
    ).status_code == 403
    assert client.put(
        "/api/settings", headers=sub, json={"google_project_id": "proj"},
    ).status_code == 403
    assert client.post(
        "/api/settings/keys/openrouter_api_key/check", headers=sub,
    ).status_code == 403

    # Delete works for the client (step one of "replace with a new one").
    resp = client.delete("/api/settings/keys/openrouter_api_key", headers=sub)
    assert resp.status_code == 200, resp.text
    assert resp.json()["openrouter_api_key"]["configured"] is False
    assert settings.openrouter_api_key == ""


def test_client_masked_hint_tells_keys_apart(saved_keys):
    """Two different OpenRouter keys produce different masks (the shared
    'sk-or' prefix must not eat the whole head)."""
    headers = auth_headers()
    client.put(
        "/api/settings", headers=headers,
        json={"openrouter_api_key": "sk-or-v1-aaaaaaaaaaaaaaaaaaaa1111"},
    )
    first = client.get("/api/settings", headers=headers).json()
    client.put(
        "/api/settings", headers=headers,
        json={"openrouter_api_key": "sk-or-v1-bbbbbbbbbbbbbbbbbbbb2222"},
    )
    second = client.get("/api/settings", headers=headers).json()
    assert first["openrouter_api_key"]["hint"] == "sk-or-v1-aaa…1111"
    assert second["openrouter_api_key"]["hint"] == "sk-or-v1-bbb…2222"
    assert first["openrouter_api_key"]["hint"] != second["openrouter_api_key"]["hint"]
    client.delete("/api/settings/keys/openrouter_api_key", headers=headers)


# ---------------------------------------------------------------------------
# Owner e-mail binding: e-mail login lands on the owner account
# ---------------------------------------------------------------------------

def test_owner_email_binding_and_email_login(saved_keys):
    """Binding an e-mail to the owner account: e-mail + master password (and
    Google, by the same lookup) resolve to the owner; a client account that
    held the address loses it; the bound session manages keys."""
    from sqlalchemy import select

    from app.models import Account

    test_email = "owner-guy@example.com"

    # A client account currently squatting on the address.
    squatter = client.post(
        "/api/auth/accounts",
        json={"admin_password": "test", "label": "squatter",
              "client_password": "pw12345"},
    )
    assert squatter.status_code == 201, squatter.text
    with SessionLocal() as db:
        acc = db.get(Account, squatter.json()["account_id"])
        acc.email = test_email
        db.commit()

    headers = auth_headers()

    # The view exposes the current binding (empty before).
    view = client.get("/api/settings", headers=headers).json()
    assert "owner_email" in view

    resp = client.post(
        "/api/settings/owner-email", headers=headers, json={"email": test_email},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_email"] == test_email

    # The squatter lost the address; the owner account now holds it.
    with SessionLocal() as db:
        assert db.get(Account, squatter.json()["account_id"]).email is None
        owner = db.scalar(select(Account).where(Account.email == test_email))
        assert owner is not None and owner.email_confirmed

    # E-mail + master password login lands on the OWNER account.
    resp = client.post(
        "/api/auth/login", json={"login": test_email, "password": "test"},
    )
    assert resp.status_code == 200, resp.text
    bound_headers = {"Authorization": f"Bearer {resp.json()['token']}"}
    me = client.get("/api/auth/me", headers=bound_headers).json()
    assert me["is_owner"] is True

    # The bound session can manage keys (paste one, then re-check it).
    resp = client.put(
        "/api/settings", headers=bound_headers, json={"tavily_api_key": "tvly-good"},
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/settings/keys/tavily_api_key/check", headers=bound_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "valid"

    # Invalid address is rejected; empty unbinds.
    assert client.post(
        "/api/settings/owner-email", headers=headers, json={"email": "nope"},
    ).status_code == 422
    assert client.post(
        "/api/settings/owner-email", headers=headers, json={"email": ""},
    ).status_code == 200

    # Client sessions still get 403 on the settings router.
    resp = client.post(
        "/api/auth/accounts",
        json={"admin_password": "test", "label": "plain-client",
              "client_password": "pw12345"},
    )
    client_login = client.post(
        "/api/auth/login",
        json={"login": squatter.json()["account_id"], "password": "pw12345"},
    ).json()
    assert client.post(
        "/api/settings/owner-email",
        headers={"Authorization": f"Bearer {client_login['token']}"},
        json={"email": "x@y.zz"},
    ).status_code == 403

    with SessionLocal() as db:
        assert db.get(Account, squatter.json()["account_id"]) is not None
