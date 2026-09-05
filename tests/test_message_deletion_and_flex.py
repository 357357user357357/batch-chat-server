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

    def fake_completion(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
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


# ---------------------------------------------------------------------------
# Cache keep-alive (cheap 1-hour TTL extension via near-empty pings)
# ---------------------------------------------------------------------------

def test_cache_keeper_pings_only_enabled_conversations(monkeypatch):
    """Warm-up is opt-in: a recorded but NOT enabled dialog is never pinged;
    enabling via the 🔥 Cache toggle starts the pings."""
    from app.services import cache_keeper
    from app.config import settings as app_settings

    cache_keeper.reset_for_tests()
    monkeypatch.setattr(app_settings, "cache_keepalive_hours", 3)
    monkeypatch.setattr(app_settings, "cache_duration_seconds", 3600)

    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers, json={"title": "keeper"}).json()
    client.post(
        f"/api/conversations/{conv['id']}/messages",
        headers=headers,
        json={"role": "user", "content": "keeper question"},
    ).json()

    # Simulate a real request having happened 46 minutes ago.
    import time as time_mod

    old_now = time_mod.time
    monkeypatch.setattr(
        cache_keeper.time, "time", lambda: old_now() - 46 * 60, raising=False
    )
    cache_keeper.record(
        conv["id"],
        "TEST SYSTEM PROMPT",
        ["openai/gpt-4o-mini", "openai/gpt-4o-mini", "anthropic/claude-fable-5.1:batch"],
    )
    monkeypatch.setattr(cache_keeper.time, "time", old_now, raising=False)

    pings: list[dict] = []

    def fake_completion(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        pings.append({"model": model, "messages": messages, "max_tokens": max_tokens})
        return "."

    monkeypatch.setattr(cache_keeper, "PING_INTERVAL_SECONDS", 60)
    monkeypatch.setattr("app.services.openrouter.chat_completion", fake_completion)

    # Not enabled → no pings (warm-up is opt-in per dialog).
    cache_keeper._tick()
    assert len(pings) == 0

    # 🔥 Cache toggle ON → due models get exactly one ping each cycle.
    cache_keeper.set_enabled(conv["id"], True)
    cache_keeper._tick()

    # ":batch" model is skipped (async-only id); the plain model got one ping.
    assert len(pings) == 1
    ping = pings[0]
    assert ping["model"] == "openai/gpt-4o-mini"
    assert ping["max_tokens"] == cache_keeper.PING_MAX_TOKENS
    # Exact recorded system prompt + stored history + the "." keep-alive turn
    assert ping["messages"][0] == {"role": "system", "content": "TEST SYSTEM PROMPT"}
    assert ping["messages"][-1] == {"role": "user", "content": "."}
    assert any(m["content"] == "keeper question" for m in ping["messages"])

    # Second tick right after → no duplicate ping (interval claim works).
    cache_keeper._tick()
    assert len(pings) == 1

    # Entries outside the keep-alive window are dropped (no more pings).
    monkeypatch.setattr(
        cache_keeper.time, "time", lambda: old_now() + 4 * 3600, raising=False
    )
    cache_keeper._tick()
    assert len(pings) == 1
    cache_keeper.reset_for_tests()


def test_keepalive_toggle_endpoint():
    """The 🔥 Cache toggle persists per dialog and is reported on fetch."""
    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers, json={"title": "ka toggle"}).json()

    resp = client.post(
        f"/api/conversations/{conv['id']}/keepalive",
        headers=headers,
        json={"enabled": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["keepalive"] is True

    detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
    assert detail["keepalive"] is True

    resp = client.post(
        f"/api/conversations/{conv['id']}/keepalive",
        headers=headers,
        json={"enabled": False},
    )
    assert resp.json()["keepalive"] is False
    detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
    assert detail["keepalive"] is False


def test_cache_keeper_pings_flex_models_with_suffix(monkeypatch):
    """:flex dialogs get keep-alive too — the ping must keep the ":flex"
    suffix so chat_completion re-applies service_tier="flex" and hits the
    same (flex-tier) cache instead of falling back to a different prefix."""
    from app.services import cache_keeper
    from app.config import settings as app_settings

    cache_keeper.reset_for_tests()
    monkeypatch.setattr(app_settings, "cache_keepalive_hours", 3)
    monkeypatch.setattr(app_settings, "cache_duration_seconds", 3600)

    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers, json={"title": "flex keeper"}).json()

    flex_model = "openai/gpt-6-astra:flex"
    pings: list[dict] = []

    def fake_completion(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        pings.append({"model": model, "messages": messages})
        return "."

    monkeypatch.setattr("app.services.openrouter.chat_completion", fake_completion)
    cache_keeper.record(conv["id"], "FLEX SYSTEM", [flex_model])
    cache_keeper.set_enabled(conv["id"], True)
    cache_keeper._tick()

    assert len(pings) == 1
    assert pings[0]["model"] == flex_model  # suffix preserved → same flex tier
    cache_keeper.reset_for_tests()


def test_cache_keeper_disabled_without_1h_cache(monkeypatch):
    """record() alone never registers for pings when the cache TTL is the
    5-minute one (a 45-min ping could never hit it anyway)."""
    from app.services import cache_keeper
    from app.config import settings as app_settings

    cache_keeper.reset_for_tests()
    monkeypatch.setattr(app_settings, "cache_duration_seconds", 300)
    monkeypatch.setattr(app_settings, "cache_keepalive_hours", 3)

    cache_keeper.record(999999, "SYSTEM", ["openai/gpt-4o-mini"])
    assert 999999 not in cache_keeper._entries
    cache_keeper.reset_for_tests()


def test_push_never_resurrects_deleted_dialogs(monkeypatch):
    """Regression for the phone's 'Sync now' re-creating deleted dialogs:
    a stale push against a tombstoned dialog is skipped; the tombstone wins
    and the pusher drops its local copy on the next pull."""
    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "redmi-note-9t"}
    dialog = {"id": "resurrect-dlg", "title": "Resurrect", "model": "m",
              "messages": [{"role": "user", "content": "old"}]}

    # 1. Phone pushes a dialog; server stores it.
    resp = client.post("/api/sync/push", headers=phone_headers,
                       json={"dialogs": [dialog], "batches": [],
                             "deleted_external_ids": [], "keys": {}})
    assert resp.status_code == 200
    assert resp.json()["created"] == 1

    # 2. The web deletes it (tombstone on the master server).
    import sqlite3

    from app.database import engine

    with sqlite3.connect(engine.url.database) as conn:
        conv_id = conn.execute(
            "SELECT id FROM conversations WHERE external_id='resurrect-dlg'"
        ).fetchone()[0]
    assert client.delete(f"/api/conversations/{conv_id}", headers={**headers, "X-Device-Name": "web-test"}).status_code == 204

    # 3. Phone (still holding its local copy) pushes the SAME dialog again —
    #    it must NOT come back to life on the server.
    resp = client.post("/api/sync/push", headers=phone_headers,
                       json={"dialogs": [dialog], "batches": [],
                             "deleted_external_ids": [], "keys": {}})
    assert resp.status_code == 200
    assert resp.json()["skipped_deleted"] == 1
    assert resp.json()["updated"] == 0

    with sqlite3.connect(engine.url.database) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT deleted_at, deleted_by, title FROM conversations WHERE id=?",
            (conv_id,),
        ).fetchone()
    assert row["deleted_at"] is not None           # still tombstoned
    assert row["deleted_by"] == "web-test"            # original deletion kept

    # 4. Pull still reports deleted:true so the phone removes its local copy.
    pulled = client.get("/api/sync/pull", headers=headers).json()
    dlg = next(c for c in pulled["conversations"] if c["external_id"] == "resurrect-dlg")
    assert dlg["deleted"] is True

    # Cleanup (hard, test-only).
    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("DELETE FROM message_tombstones WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def test_reasoning_effort_reaches_openrouter_payload(monkeypatch):
    """Reasoning effort is forwarded as OpenRouter's unified reasoning param:
    'none' → {"enabled": false}; levels → {"effort": level}; default → absent."""
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
            calls.append(dict(json))
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    chat_completion("openai/gpt-6-astra", [{"role": "user", "content": "hi"}])
    assert "reasoning" not in calls[-1]  # default: nothing sent

    chat_completion("openai/gpt-6-astra", [{"role": "user", "content": "hi"}],
                    reasoning_effort="none")
    assert calls[-1]["reasoning"] == {"enabled": False}

    chat_completion("openai/gpt-6-astra", [{"role": "user", "content": "hi"}],
                    reasoning_effort="xhigh")
    assert calls[-1]["reasoning"] == {"effort": "xhigh"}


def test_chat_send_rejects_invalid_reasoning_effort():
    """The API validates the reasoning effort against the allowed levels."""
    headers = auth_headers()
    resp = client.post("/api/chat/send", headers=headers, json={
        "user_message": "hi", "models": ["openai/gpt-6-astra"],
        "reasoning_effort": "ultra",
    })
    assert resp.status_code == 422


def test_sync_audit_trail_attribution():
    """Master-server archive: every record remembers whose it was, who last
    modified it, and when/by whom it was deleted — and soft-deleted records
    stay in the master DB (tombstoned, never wiped)."""
    import sqlite3

    from app.database import engine

    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "redmi-note-9t"}

    # 1. Phone pushes a dialog → origin_device = the phone.
    resp = client.post(
        "/api/sync/push",
        headers=phone_headers,
        json={"dialogs": [{"id": "audit-dlg", "title": "Audit", "model": "m",
                           "messages": [{"role": "user", "content": "hi"},
                                        {"role": "assistant", "content": "hello"}]}],
              "batches": [], "deleted_external_ids": [], "keys": {}},
    )
    assert resp.status_code == 200, resp.text

    pulled = client.get("/api/sync/pull", headers=headers).json()
    dlg = next(c for c in pulled["conversations"] if c["external_id"] == "audit-dlg")
    assert dlg["origin_device"] == "redmi-note-9t"
    assert dlg["modified_by"] == "redmi-note-9t"
    assert dlg["deleted"] is False

    # 2. Web deletes it → deleted_at + deleted_by = web, record stays archived.
    with sqlite3.connect(engine.url.database) as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE external_id='audit-dlg'"
        ).fetchone()
    conv_id = row[0]
    resp = client.delete(
        f"/api/conversations/{conv_id}",
        headers={**headers, "User-Agent": "Mozilla/5.0 web-test"},
    )
    assert resp.status_code == 204

    with sqlite3.connect(engine.url.database) as conn:
        conn.row_factory = sqlite3.Row
        archived = conn.execute(
            "SELECT deleted_at, deleted_by, origin_device, title "
            "FROM conversations WHERE id=?",
            (conv_id,),
        ).fetchone()
    assert archived["deleted_at"] is not None            # deletion DATE recorded
    assert archived["deleted_by"] == "web"               # WHO deleted it
    assert archived["origin_device"] == "redmi-note-9t"  # WHOSE record it was
    assert archived["title"] == "Audit"                  # content kept (archive)

    # 3. Pull shows the tombstone with the audit fields (devices drop it).
    pulled = client.get("/api/sync/pull", headers=headers).json()
    dlg = next(c for c in pulled["conversations"] if c["external_id"] == "audit-dlg")
    assert dlg["deleted"] is True
    assert dlg["deleted_at"] is not None
    assert dlg["deleted_by"] == "web"
    assert dlg["origin_device"] == "redmi-note-9t"

    # Cleanup (hard delete, test-only).
    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("DELETE FROM message_tombstones WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def test_message_deletion_records_deleted_by():
    """Per-message removal stores who deleted the Q/A (web or phone)."""
    import sqlite3

    from app.database import engine

    headers = auth_headers()
    conv = client.post("/api/conversations", headers=headers,
                       json={"title": "audit-msg"}).json()
    msg = client.post(f"/api/conversations/{conv['id']}/messages", headers=headers,
                      json={"role": "user", "content": "to be removed"}).json()
    resp = client.delete(
        f"/api/conversations/{conv['id']}/messages/{msg['id']}",
        headers={**headers, "User-Agent": "Mozilla/5.0 web-test"},
    )
    assert resp.status_code == 204

    with sqlite3.connect(engine.url.database) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT deleted_at, deleted_by FROM messages WHERE id=?", (msg["id"],)
        ).fetchone()
        tomb = conn.execute(
            "SELECT deleted_by FROM message_tombstones WHERE conversation_id=?",
            (conv["id"],),
        ).fetchone()
    assert row["deleted_at"] is not None
    assert row["deleted_by"] == "web"
    assert tomb is not None and tomb["deleted_by"] == "web"

    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("DELETE FROM message_tombstones WHERE conversation_id=?", (conv["id"],))
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv["id"],))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv["id"],))