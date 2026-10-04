"""RikkaHub-style personas: CRUD, attachment, and ride-along behavior."""

import os

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/bc_test_batch.db")

import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def sse_data(resp) -> dict:
    events = [
        line[len("data: "):]
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events, f"no SSE data event in: {resp.text[:400]}"
    return json.loads(events[-1])


def auth_headers() -> dict:
    resp = client.post("/api/auth/login", json={"password": "test"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def make_persona(**overrides) -> dict:
    payload = {
        "name": "Math tutor",
        "system_prompt": "You are a patient math tutor.",
        "temperature": 0.2,
        **overrides,
    }
    resp = client.post("/api/personas", headers=auth_headers(), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def capture_send(monkeypatch, calls) -> None:
    from app.routers import chat as chat_router

    def fake(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        calls.append({"model": model, "messages": messages, "temperature": temperature})
        return {"content": "ok"}

    monkeypatch.setattr(chat_router, "chat_completion_full", fake)


def test_persona_crud():
    headers = auth_headers()
    persona = make_persona()
    assert persona["name"] == "Math tutor"
    assert persona["temperature"] == 0.2

    listed = client.get("/api/personas", headers=headers).json()
    assert any(p["id"] == persona["id"] for p in listed)

    patched = client.patch(
        f"/api/personas/{persona['id']}", headers=headers,
        json={"name": "Algebra coach", "temperature": 0.5},
    ).json()
    assert patched["name"] == "Algebra coach"
    assert patched["system_prompt"] == "You are a patient math tutor."  # unset = unchanged
    assert patched["temperature"] == 0.5

    assert client.delete(f"/api/personas/{persona['id']}", headers=headers).status_code == 204
    assert all(
        p["id"] != persona["id"]
        for p in client.get("/api/personas", headers=headers).json()
    )
    assert client.patch(
        f"/api/personas/{persona['id']}", headers=headers, json={"name": "x"}
    ).status_code == 404


def test_persona_validation():
    headers = auth_headers()
    assert client.post("/api/personas", headers=headers, json={"name": ""}).status_code == 422
    assert client.post(
        "/api/personas", headers=headers, json={"name": "x", "temperature": 5}
    ).status_code == 422


def test_conversation_persona_attach_and_clear():
    headers = auth_headers()
    persona = make_persona(name="Coder")
    conv = client.post("/api/conversations", headers=headers, json={"title": "T"}).json()

    detail = client.put(
        f"/api/conversations/{conv['id']}/persona", headers=headers,
        json={"persona_id": persona["id"]},
    ).json()
    assert detail["persona_id"] == persona["id"]

    assert client.put(
        f"/api/conversations/{conv['id']}/persona", headers=headers,
        json={"persona_id": "nope"},
    ).status_code == 404

    detail = client.put(
        f"/api/conversations/{conv['id']}/persona", headers=headers,
        json={"persona_id": None},
    ).json()
    assert detail["persona_id"] is None


def test_persona_rides_along_on_send(monkeypatch):
    headers = auth_headers()
    persona = make_persona(name="Tutor", system_prompt="You tutor math.", temperature=0.3)
    conv = client.post("/api/conversations", headers=headers, json={"title": "S"}).json()
    client.put(
        f"/api/conversations/{conv['id']}/persona", headers=headers,
        json={"persona_id": persona["id"]},
    )

    calls: list = []
    capture_send(monkeypatch, calls)
    resp = client.post(
        "/api/chat/send", headers=headers,
        json={"user_message": "hi", "models": ["openai/gpt-test"],
              "conversation_id": conv["id"]},
    )
    assert resp.status_code == 200, resp.text
    assert calls, "provider was never called"
    first = calls[0]["messages"][0]
    assert first["role"] == "system"
    assert "You tutor math." in first["content"]  # build_answer_system appends server time
    assert calls[0]["temperature"] == 0.3


def test_explicit_system_overrides_persona(monkeypatch):
    headers = auth_headers()
    persona = make_persona(name="Tutor2", system_prompt="PERSONA PROMPT")
    conv = client.post("/api/conversations", headers=headers, json={"title": "S2"}).json()
    client.put(
        f"/api/conversations/{conv['id']}/persona", headers=headers,
        json={"persona_id": persona["id"]},
    )

    calls: list = []
    capture_send(monkeypatch, calls)
    resp = client.post(
        "/api/chat/send", headers=headers,
        json={"user_message": "hi", "models": ["openai/gpt-test"],
              "conversation_id": conv["id"], "system": "EXPLICIT PROMPT"},
    )
    assert resp.status_code == 200
    system_msgs = [m for c in calls for m in c["messages"] if m["role"] == "system"]
    assert any("EXPLICIT PROMPT" in m["content"] for m in system_msgs)
    assert not any("PERSONA PROMPT" in m["content"] for m in system_msgs)


def test_send_with_persona_id_attaches_to_new_conversation(monkeypatch):
    headers = auth_headers()
    persona = make_persona(name="Scout", system_prompt="SCOUT PROMPT")
    calls: list = []
    capture_send(monkeypatch, calls)
    resp = client.post(
        "/api/chat/send", headers=headers,
        json={"user_message": "hello", "models": ["openai/gpt-test"],
              "persona_id": persona["id"]},
    )
    assert resp.status_code == 200
    assert calls[0]["messages"][0]["role"] == "system"
    assert "SCOUT PROMPT" in calls[0]["messages"][0]["content"]
    conv_id = sse_data(resp)["conversation_id"]
    detail = client.get(f"/api/conversations/{conv_id}", headers=headers).json()
    assert detail["persona_id"] == persona["id"]  # the persona stuck

    assert client.post(
        "/api/chat/send", headers=headers,
        json={"user_message": "x", "models": ["openai/gpt-test"], "persona_id": "missing"},
    ).status_code == 404


def test_deleted_persona_stops_riding_along(monkeypatch):
    headers = auth_headers()
    persona = make_persona(name="Ghost", system_prompt="GHOST PROMPT")
    conv = client.post("/api/conversations", headers=headers, json={"title": "G"}).json()
    client.put(
        f"/api/conversations/{conv['id']}/persona", headers=headers,
        json={"persona_id": persona["id"]},
    )
    client.delete(f"/api/personas/{persona['id']}", headers=headers)

    detail = client.get(f"/api/conversations/{conv['id']}", headers=headers).json()
    assert detail["persona_id"] is None  # delete clears the pointer

    calls: list = []
    capture_send(monkeypatch, calls)
    resp = client.post(
        "/api/chat/send", headers=headers,
        json={"user_message": "hi", "models": ["openai/gpt-test"],
              "conversation_id": conv["id"]},
    )
    assert resp.status_code == 200
    assert calls[0]["temperature"] is None  # no persona, no ambient temperature


def test_retry_uses_conversation_persona(monkeypatch):
    headers = auth_headers()
    persona = make_persona(name="Re", system_prompt="RETRY PERSONA")
    conv = client.post("/api/conversations", headers=headers, json={"title": "R"}).json()
    q = client.post(
        f"/api/conversations/{conv['id']}/messages", headers=headers,
        json={"role": "user", "content": "Q?"},
    ).json()
    a = client.post(
        f"/api/conversations/{conv['id']}/messages", headers=headers,
        json={"role": "assistant", "content": "A."},
    ).json()
    client.put(
        f"/api/conversations/{conv['id']}/persona", headers=headers,
        json={"persona_id": persona["id"]},
    )

    calls: list = []
    from app.routers import chat as chat_router

    def fake(model, messages, temperature=None, max_tokens=None, reasoning_effort=None):
        calls.append({"model": model, "messages": messages})
        return {"content": "again"}

    monkeypatch.setattr(chat_router, "chat_completion_full", fake)

    resp = client.post(
        "/api/chat/retry", headers=headers,
        json={"conversation_id": conv["id"], "message_id": a["id"],
              "models": ["openai/gpt-test"]},
    )
    assert resp.status_code == 200, resp.text
    assert calls[0]["messages"][0]["role"] == "system"
    assert "RETRY PERSONA" in calls[0]["messages"][0]["content"]
