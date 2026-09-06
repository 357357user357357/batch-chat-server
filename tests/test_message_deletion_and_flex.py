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

    # Every attempt is recorded in the verification ping log (ok entry).
    log = cache_keeper.ping_log()
    assert len(log) == 1
    assert log[0]["conversation_id"] == conv["id"]
    assert log[0]["model"] == "openai/gpt-4o-mini"
    assert log[0]["ok"] is True

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


def test_keepalive_pings_endpoint_reports_log():
    """The verification feed lists recent pings (with dialog titles) without
    any stub dialogs ever being created."""
    from app.services import cache_keeper
    from app.config import settings as app_settings

    cache_keeper.reset_for_tests()
    monkeypatch_free_headers = auth_headers()
    conv = client.post("/api/conversations", headers=monkeypatch_free_headers,
                       json={"title": "ping-feed"}).json()
    # Enable warming through the API so the DB flag (the feed's source of
    # truth) is set, like the web UI does.
    resp = client.post(f"/api/conversations/{conv['id']}/keepalive",
                       headers=monkeypatch_free_headers, json={"enabled": True})
    assert resp.status_code == 200

    # Simulate one successful ping attempt via the keeper's own log.
    cache_keeper.record(conv["id"], "SYS", ["openai/gpt-4o-mini"])
    import time as time_mod

    real_time = time_mod.time
    monkeypatch_old = cache_keeper.time.time
    cache_keeper.time.time = lambda: real_time() - 46 * 60

    def fake_completion(model, messages, temperature=None, max_tokens=None,
                        reasoning_effort=None):
        return "."

    import app.services.openrouter as _or

    saved = _or.chat_completion
    _or.chat_completion = fake_completion
    try:
        cache_keeper._tick()
    finally:
        _or.chat_completion = saved
        cache_keeper.time.time = monkeypatch_old

    resp = client.get("/api/conversations/keepalive/pings",
                      headers=monkeypatch_free_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["interval_minutes"] == 45
    assert {"conversation_id": conv["id"], "title": "ping-feed"} in data["enabled"]
    assert len(data["pings"]) == 1
    ping = data["pings"][0]
    assert ping["ok"] is True
    assert ping["title"] == "ping-feed"
    assert ping["model"] == "openai/gpt-4o-mini"
    cache_keeper.reset_for_tests()


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
    phone_headers = {**headers, "X-Device-Name": "test-phone"}
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
    'none' → {"enabled": false}; levels → {"effort": level}; default → absent.
    If the model rejects reasoning (mandatory/no support), the request is
    retried once WITHOUT the param (model default applies)."""
    calls = []

    class FakeResponse:
        def __init__(self, status_code=200):
            self.status_code = status_code

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
            # Reject "none" the way reasoning-mandatory models (astra) do.
            if json.get("reasoning") == {"enabled": False}:
                return FakeResponse(400)
            return FakeResponse(200)

    def _safe_error(resp):
        return "Reasoning is mandatory for this endpoint and cannot be disabled."

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr("app.services.openrouter._safe_error", _safe_error)

    chat_completion("openai/gpt-6-astra", [{"role": "user", "content": "hi"}])
    assert "reasoning" not in calls[-1]  # default: nothing sent

    chat_completion("openai/gpt-6-astra", [{"role": "user", "content": "hi"}],
                    reasoning_effort="xhigh")
    assert calls[-1]["reasoning"] == {"effort": "xhigh"}  # accepted as-is

    answer = chat_completion("openai/gpt-6-astra",
                             [{"role": "user", "content": "hi"}],
                             reasoning_effort="none")
    assert answer == "ok"
    assert calls[-2]["reasoning"] == {"enabled": False}   # first attempt
    assert "reasoning" not in calls[-1]                   # fallback retry


def test_chat_send_rejects_invalid_reasoning_effort():
    """The API validates the reasoning effort against the allowed levels."""
    headers = auth_headers()
    resp = client.post("/api/chat/send", headers=headers, json={
        "user_message": "hi", "models": ["openai/gpt-6-astra"],
        "reasoning_effort": "ultra",
    })
    assert resp.status_code == 422


def test_pull_always_delivers_tombstones_regardless_of_since():
    """Deletions must reach every device even if its `since` cursor is newer
    than the deletion (e.g. the device synced after the deletion but failed to
    apply it): tombstones are re-delivered on every pull until wiped."""
    import sqlite3

    from app.database import engine

    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "test-phone"}
    dialog = {"id": "tombstone-always", "title": "AlwaysDeliver", "model": "m",
              "messages": [{"role": "user", "content": "x"}]}

    resp = client.post("/api/sync/push", headers=phone_headers,
                       json={"dialogs": [dialog], "batches": [],
                             "deleted_external_ids": [], "keys": {}})
    assert resp.status_code == 200

    with sqlite3.connect(engine.url.database) as conn:
        conv_id = conn.execute(
            "SELECT id FROM conversations WHERE external_id='tombstone-always'"
        ).fetchone()[0]
    assert client.delete(f"/api/conversations/{conv_id}", headers=headers).status_code == 204

    # A `since` stamp NEWER than the deletion must still deliver the tombstone.
    later = client.get("/api/sync/pull", headers=headers).json()["server_time"]
    pulled = client.get(f"/api/sync/pull?since={later}", headers=headers).json()
    dlg = next(c for c in pulled["conversations"] if c["external_id"] == "tombstone-always")
    assert dlg["deleted"] is True

    # Cleanup (hard, test-only).
    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("DELETE FROM message_tombstones WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def test_sync_audit_trail_attribution():
    """Master-server archive: every record remembers whose it was, who last
    modified it, and when/by whom it was deleted — and soft-deleted records
    stay in the master DB (tombstoned, never wiped)."""
    import sqlite3

    from app.database import engine

    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "test-phone"}

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
    assert dlg["origin_device"] == "test-phone"
    assert dlg["modified_by"] == "test-phone"
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
    assert archived["origin_device"] == "test-phone"  # WHOSE record it was
    assert archived["title"] == "Audit"                  # content kept (archive)

    # 3. Pull shows the tombstone with the audit fields (devices drop it).
    pulled = client.get("/api/sync/pull", headers=headers).json()
    dlg = next(c for c in pulled["conversations"] if c["external_id"] == "audit-dlg")
    assert dlg["deleted"] is True
    assert dlg["deleted_at"] is not None
    assert dlg["deleted_by"] == "web"
    assert dlg["origin_device"] == "test-phone"

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

def test_models_endpoint_includes_pricing(monkeypatch):
    """The model picker endpoint exposes per-token pricing PER CHAT MODE for
    the picker models: live = catalog price, flex/batch = 50% discount."""
    import app.routers.chat as chat_router
    import app.services.openrouter as openrouter

    monkeypatch.setattr(
        openrouter,
        "fetch_model_pricing",
        lambda: {
            "openai/gpt-6-astra": {"prompt": 2e-06, "completion": 8e-06},
            "anthropic/claude-fable-5.1": {"prompt": 3e-06, "completion": 1.5e-05},
            "~deepseek/deepseek-v4-flash-latest": {"prompt": 2.7e-07, "completion": 1.1e-06},
        },
        raising=False,
    )
    monkeypatch.setattr(
        chat_router,
        "default_models",
        lambda: [
            "openai/gpt-6-astra",
            "~deepseek/deepseek-v4-flash-latest",
            "anthropic/claude-fable-5.1:batch",
        ],
        raising=False,
    )
    data = client.get("/api/chat/models").json()
    # Live tier = plain catalog price.
    live = data["pricing"]["openai/gpt-6-astra"]["live"]
    assert live["prompt"] == 2e-06 and live["completion"] == 8e-06
    # Flex and Batch tiers cost 50% of the standard price.
    for mode in ("flex", "batch"):
        tier = data["pricing"]["openai/gpt-6-astra"][mode]
        assert tier["prompt"] == 1e-06 and tier["completion"] == 4e-06
    # DeepSeek's floating alias resolves through its plain catalog id.
    assert "~deepseek/deepseek-v4-flash-latest" in data["pricing"]
    # An explicit async batch id is priced per tier as well.
    batch = data["pricing"]["anthropic/claude-fable-5.1:batch"]["batch"]
    assert batch["prompt"] == 1.5e-06 and batch["completion"] == 7.5e-06


def test_sync_push_merges_web_added_messages(monkeypatch):
    """Regression: a stale phone push must NOT wipe messages added on the web
    after the phone's last sync. The pushed dialog's updated_at is older than
    the web message's created_at -> the web message is kept and appended."""
    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "test-phone"}
    dialog = {"id": "merge-dlg", "title": "Merge", "model": "m",
              "messages": [{"role": "user", "content": "q1"}]}

    # 1. Phone pushes the dialog.
    assert client.post("/api/sync/push", headers=phone_headers,
                       json={"dialogs": [dialog], "batches": [],
                             "deleted_external_ids": [], "keys": {}}
                       ).status_code == 200

    # 2. Web adds a new message to the same dialog.
    conv_id = None
    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    ext = next(c for c in pulled if c["external_id"] == "merge-dlg")["external_id"]
    import sqlite3
    from app.database import engine
    with sqlite3.connect(engine.url.database) as conn:
        conv_id = conn.execute(
            "SELECT id FROM conversations WHERE external_id='merge-dlg'"
        ).fetchone()[0]
    added = client.post(f"/api/conversations/{conv_id}/messages", headers=headers,
                        json={"role": "assistant", "content": "web answer"}).json()
    assert added["id"]

    # 3. Phone syncs again with its STALE local copy (no web answer, updated_at
    #    older than the web message) -> the web answer must survive.
    stale = {"id": "merge-dlg", "title": "Merge", "model": "m",
             "messages": [{"role": "user", "content": "q1"}],
             "updatedAt": 1}  # epoch ms, far in the past
    resp = client.post("/api/sync/push", headers=phone_headers,
                       json={"dialogs": [stale], "batches": [],
                             "deleted_external_ids": [], "keys": {}})
    assert resp.status_code == 200

    detail = client.get(f"/api/conversations/{conv_id}", headers=headers).json()
    contents = [m["content"] for m in detail["messages"]]
    assert contents == ["q1", "web answer"]

    # Cleanup (hard, test-only).
    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("DELETE FROM message_tombstones WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def test_sync_push_append_only_even_with_future_stamp(monkeypatch):
    """Regression: the merge must be APPEND-ONLY. A stale phone push whose
    updated_at stamp is NEWER than a web-added message's created_at (the phone
    re-stamps dialogs from the server after every sync) used to archive the
    web message. Absence from a push must never remove a server message."""
    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "test-phone"}
    dialog = {"id": "append-dlg", "title": "Append", "model": "m",
              "messages": [{"role": "user", "content": "q1"}]}

    assert client.post("/api/sync/push", headers=phone_headers,
                       json={"dialogs": [dialog], "batches": [],
                             "deleted_external_ids": [], "keys": {}}).status_code == 200

    import sqlite3
    from app.database import engine
    with sqlite3.connect(engine.url.database) as conn:
        conv_id = conn.execute(
            "SELECT id FROM conversations WHERE external_id='append-dlg'"
        ).fetchone()[0]
    added = client.post(f"/api/conversations/{conv_id}/messages", headers=headers,
                        json={"role": "assistant", "content": "web answer 2"}).json()
    assert added["id"]

    # Stale copy WITHOUT the web answer, stamped far in the FUTURE — the exact
    # case that used to wipe the message. It must survive untouched.
    stale = {"id": "append-dlg", "title": "Append", "model": "m",
             "messages": [{"role": "user", "content": "q1"}],
             "updatedAt": 99999999999999}  # epoch ms, year ~5138
    resp = client.post("/api/sync/push", headers=phone_headers,
                       json={"dialogs": [stale], "batches": [],
                             "deleted_external_ids": [], "keys": {}})
    assert resp.status_code == 200

    detail = client.get(f"/api/conversations/{conv_id}", headers=headers).json()
    contents = [m["content"] for m in detail["messages"]]
    assert contents == ["q1", "web answer 2"]

    # Cleanup (hard, test-only).
    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("DELETE FROM message_tombstones WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def test_sync_message_delete_endpoint():
    """Phone deletion by external_id: tombstones the message server-side,
    excludes it from pulls, records deleted_by, and survives stale pushes."""
    headers = auth_headers()
    phone_headers = {**headers, "X-Device-Name": "test-phone"}

    assert client.post("/api/sync/push", headers=phone_headers, json={
        "dialogs": [{"id": "phone-del-dlg", "title": "PhDel", "model": "m",
                     "messages": [{"role": "user", "content": "keep"},
                                  {"role": "assistant", "content": "drop"}]}],
        "batches": [], "deleted_external_ids": [], "keys": {}}).status_code == 200

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    dlg = next(c for c in pulled if c["external_id"] == "phone-del-dlg")
    drop_id = next(m["id"] for m in dlg["messages"] if m["content"] == "drop")

    resp = client.delete(
        f"/api/sync/dialogs/phone-del-dlg/messages/{drop_id}", headers=phone_headers)
    assert resp.status_code == 200 and resp.json()["ok"] is True

    pulled = client.get("/api/sync/pull", headers=headers).json()["conversations"]
    dlg = next(c for c in pulled if c["external_id"] == "phone-del-dlg")
    contents = [m["content"] for m in dlg["messages"]]
    assert contents == ["keep"]

    # Audit trail on the master DB.
    import sqlite3
    from app.database import engine
    with sqlite3.connect(engine.url.database) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT deleted_at, deleted_by FROM messages WHERE id=?", (drop_id,)
        ).fetchone()
    assert row["deleted_at"] is not None and row["deleted_by"] == "test-phone"

    # 404 for an unknown dialog/message.
    assert client.delete(
        "/api/sync/dialogs/nope/messages/1", headers=headers).status_code == 404

    # Cleanup (hard, test-only).
    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("DELETE FROM message_tombstones WHERE conversation_id=?", (dlg_id := conn.execute(
            "SELECT id FROM conversations WHERE external_id='phone-del-dlg'").fetchone()[0],))
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (dlg_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (dlg_id,))


# ---------------------------------------------------------------
# Portable account identity: pair a device without the password.
# ---------------------------------------------------------------


def test_account_is_stable_and_pair_code_works():
    """The account id/key are generated once and survive restarts; a device
    holding the pairing code gets a normal session without the password."""
    headers = auth_headers()

    acc1 = client.get("/api/auth/account", headers=headers).json()
    acc2 = client.get("/api/auth/account", headers=headers).json()
    assert acc1["account_id"] == acc2["account_id"]
    assert acc1["account_key"] == acc2["account_key"]
    assert acc1["account_id"].startswith("bc-")
    assert acc1["account_id"] in acc1["pair_code"]

    # Pair using the full code, exactly as the phone pastes it.
    paired = client.post("/api/auth/pair", json={"code": acc1["pair_code"]})
    assert paired.status_code == 200, paired.text
    me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {paired.json()['token']}"},
    )
    assert me.status_code == 200

    # A bare id|key code (no server prefix) pairs too.
    bare = client.post(
        "/api/auth/pair",
        json={"code": f"{acc1['account_id']}|{acc1['account_key']}"},
    )
    assert bare.status_code == 200

    # Wrong code is rejected like a wrong password.
    bad = client.post("/api/auth/pair", json={"code": "bc-nope|wrong-key"})
    assert bad.status_code == 401

    # The account endpoint requires authentication.
    assert client.get("/api/auth/account").status_code == 401


def test_account_rotation_invalidates_old_codes():
    """Rotating issues a fresh key: old pair codes stop working, the id stays,
    and the new code pairs successfully."""
    headers = auth_headers()
    old = client.get("/api/auth/account", headers=headers).json()

    new = client.post("/api/auth/account/rotate", headers=headers).json()
    assert new["account_id"] == old["account_id"]
    assert new["account_key"] != old["account_key"]

    assert (
        client.post("/api/auth/pair", json={"code": old["pair_code"]}).status_code
        == 401
    )
    fresh = client.post("/api/auth/pair", json={"code": new["pair_code"]})
    assert fresh.status_code == 200


# ---------------------------------------------------------------------------
# Multi-account support: isolated clients, per-account data, owner gating.
# ---------------------------------------------------------------------------


def _pair_headers(pair_code: str) -> dict:
    resp = client.post("/api/auth/pair", json={"code": pair_code})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_multi_account_isolation_and_pairing():
    # Owner logs in with the master password (single account → no id needed).
    owner = auth_headers()
    # Owner's dialog.
    conv_owner = client.post(
        "/api/conversations", headers=owner, json={"title": "owner dlg"}
    ).json()
    # Create an isolated client account with the master password.
    created = client.post(
        "/api/auth/accounts",
        json={"admin_password": "test", "label": "uniqcheck", "client_password": "alice-pw-1"},
    )
    assert created.status_code == 201, created.text
    pair_code = created.json()["pair_code"]
    alice = _pair_headers(pair_code)
    # Alice pairs through the code and gets her own account id (not owner's).
    me = client.get("/api/auth/me", headers=alice).json()
    assert me["account_id"] != client.get("/api/auth/account", headers=owner).json()["account_id"]
    # Alice sees no owner dialogs and creates her own.
    assert client.get("/api/conversations", headers=alice).json() == []
    conv_alice = client.post(
        "/api/conversations", headers=alice, json={"title": "alice dlg"}
    ).json()
    # Cross-account reads are impossible: foreign dialog == missing (404).
    assert client.get(f"/api/conversations/{conv_owner['id']}", headers=alice).status_code == 404
    assert client.get(f"/api/conversations/{conv_alice['id']}", headers=owner).status_code == 404
    # Listings contain only each account's own dialogs.
    owner_titles = [c["title"] for c in client.get("/api/conversations", headers=owner).json()]
    alice_titles = [c["title"] for c in client.get("/api/conversations", headers=alice).json()]
    assert "alice dlg" not in owner_titles
    assert "owner dlg" not in alice_titles
    # Chat into a foreign dialog is impossible.
    chat = client.post(
        "/api/chat/send",
        headers=alice,
        json={"user_message": "hi", "models": ["m"], "conversation_id": conv_owner["id"]},
    )
    assert chat.status_code == 404
    # Push/pull scope: alice pushes a dialog, owner's pull must not see it.
    pushed = client.post(
        "/api/sync/push",
        headers=alice,
        json={"dialogs": [{"id": "alice-local-1", "title": "alice sync",
                           "model": "m", "messages": [{"role": "user", "content": "q"}]}]},
    )
    assert pushed.status_code == 200, pushed.text
    owner_ext = {c["external_id"] for c in client.get("/api/sync/pull", headers=owner).json()["conversations"]}
    alice_ext = {c["external_id"] for c in client.get("/api/sync/pull", headers=alice).json()["conversations"]}
    assert "alice-local-1" not in owner_ext
    assert "alice-local-1" in alice_ext


def test_settings_owner_only_and_backup():
    owner = auth_headers()
    created = client.post(
        "/api/auth/accounts", json={"admin_password": "test", "label": "bob", "client_password": "bob-pw-123"}
    )
    assert created.status_code == 201
    bob = _pair_headers(created.json()["pair_code"])
    # A secondary client account can never read or change provider keys.
    assert client.get("/api/settings", headers=bob).status_code == 403
    assert client.put("/api/settings", headers=bob, json={"openrouter_api_key": "x"}).status_code == 403
    assert client.get("/api/settings/backup", headers=bob).status_code == 403
    assert client.get("/api/settings", headers=owner).status_code == 200
    # Wrong master password is rejected (and counted as a login failure).
    assert client.post(
        "/api/auth/accounts", json={"admin_password": "nope", "label": "x", "client_password": "x-pw-123"}
    ).status_code == 401
    # The account list shows all accounts without secrets.
    listed = client.get("/api/auth/accounts", headers=owner).json()
    assert len(listed) >= 3  # owner + alice + bob
    assert all("account_key" not in a for a in listed)


def test_login_without_account_id_lands_on_owner():
    # Even with several accounts present (created above), a bare master-
    # password login always resolves to the OWNER account — the master
    # password is the owner's credential.
    owner = auth_headers()
    owner_id = client.get("/api/auth/account", headers=owner).json()["account_id"]
    bare = client.post("/api/auth/login", json={"password": "test"})
    assert bare.status_code == 200
    me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {bare.json()['token']}"},
    ).json()
    assert me["account_id"] == owner_id
    # Every client now has a mandatory password, so the master password can
    # no longer open a client account — it is only the owner's credential.
    listed = client.get("/api/auth/accounts", headers=owner).json()
    other = next(a["account_id"] for a in listed if a["account_id"] != owner_id)
    denied = client.post("/api/auth/login", json={"password": "test", "account_id": other})
    assert denied.status_code == 401
    # Unknown account id → 404.
    assert client.post(
        "/api/auth/login", json={"password": "test", "account_id": "bc-nope"}
    ).status_code == 404


def test_client_labels_are_unique():
    """Creating a client with a label that already exists (any case) → 409."""
    owner = auth_headers()
    r1 = client.post(
        "/api/auth/accounts",
        headers=owner,
        json={"admin_password": "test", "label": "SoloLabel", "client_password": "solo-pw-1"},
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/api/auth/accounts",
        headers=owner,
        json={"admin_password": "test", "label": "sololabel", "client_password": "solo-pw-2"},
    )
    assert r2.status_code == 409
    assert "already taken" in r2.json()["detail"]


def test_client_password_login():
    """A client created with its own password logs in with
    Account ID + that password; the master password no longer opens it."""
    owner = auth_headers()
    created = client.post(
        "/api/auth/accounts",
        headers=owner,
        json={
            "admin_password": "test",
            "label": "PwClient",
            "client_password": "client-secret-1",
        },
    ).json()
    acct = created["account_id"]
    # Own password works.
    ok = client.post(
        "/api/auth/login", json={"password": "client-secret-1", "account_id": acct}
    )
    assert ok.status_code == 200, ok.text
    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {ok.json()['token']}"}
    ).json()
    assert me["account_id"] == acct
    # Master password no longer opens this account.
    denied = client.post(
        "/api/auth/login", json={"password": "test", "account_id": acct}
    )
    assert denied.status_code == 401
    # Listing marks it as password-protected.
    row = next(
        a for a in client.get("/api/auth/accounts", headers=owner).json()
        if a["account_id"] == acct
    )
    assert row["has_password"] is True
    assert row["label"] == "PwClient"


def test_self_registration_login_and_isolation():
    """A client without an account self-registers with a unique Login +
    mandatory password, lands in its own isolated account, and logs in
    later with Login + password (case-insensitive)."""
    r = client.post(
        "/api/auth/register",
        json={"login": "NewClient", "password": "reg-pw-123"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    # Its own account, no owner dialogs visible.
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["account_id"] != client.get(
        "/api/auth/account", headers=auth_headers()
    ).json()["account_id"]
    assert client.get("/api/conversations", headers=headers).json() == []
    # Login again with the same login (different case) + password.
    again = client.post(
        "/api/auth/login", json={"login": "newclient", "password": "reg-pw-123"}
    )
    assert again.status_code == 200, again.text
    me2 = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {again.json()['token']}"}
    ).json()
    assert me2["account_id"] == me["account_id"]
    # Wrong password → 401; duplicate login → 409.
    assert client.post(
        "/api/auth/login", json={"login": "NewClient", "password": "wrong-pw"}
    ).status_code == 401
    assert client.post(
        "/api/auth/register", json={"login": "newclient", "password": "whatever-1"}
    ).status_code == 409
    # Password is mandatory: too-short password → validation error (422).
    assert client.post(
        "/api/auth/register", json={"login": "Shorty", "password": "123"}
    ).status_code == 422
