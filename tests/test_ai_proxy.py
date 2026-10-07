"""Tests for the OpenRouter passthrough proxy (/or/...).

The app points its OpenRouter provider at the server so phones behind
403-happy (RU) IPs can still reach OpenRouter via the server's non-RU egress.
These tests pin the contract: auth required, path/query forwarded, bodies
forwarded verbatim, upstream status preserved.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


class FakeUpstream:
    """Canned httpx upstream response (read + stream interface)."""

    def __init__(self, status, content, content_type="application/json"):
        self.status_code = status
        self._content = content
        self.headers = {"content-type": content_type}

    async def aread(self):
        return self._content

    async def aclose(self):
        return None

    async def aiter_bytes(self):
        yield self._content


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _patch_send(monkeypatch, responder):
    """Replace the upstream httpx send with a canned responder."""
    import app.routers.ai_proxy as mod

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def build_request(self, method, url, **kw):
            return httpx.Request(method, url, **kw)

        async def send(self, request, stream=False):
            return responder(request)

        async def aclose(self):
            return None

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)


def test_proxy_requires_authorization(client):
    response = client.get("/or/v1/models")
    assert response.status_code == 401
    assert "Authorization" in response.json()["error"]["message"]


def test_proxy_forwards_path_query_and_auth(monkeypatch, client):
    seen = {}

    def responder(request):
        seen["url"] = request.url
        seen["auth"] = request.headers.get("Authorization")
        return FakeUpstream(200, b'{"data": []}')

    _patch_send(monkeypatch, responder)
    response = client.get(
        "/or/v1/models?category=programming",
        headers={"Authorization": "Bearer sk-test"},
    )
    assert response.status_code == 200
    assert response.json() == {"data": []}
    assert str(seen["url"]).startswith("https://openrouter.ai/api/v1/models")
    assert "category=programming" in str(seen["url"])
    assert seen["auth"] == "Bearer sk-test"


def test_proxy_forwards_post_body_and_upstream_status(monkeypatch, client):
    seen = {}

    def responder(request):
        seen["body"] = request.content
        return FakeUpstream(402, b'{"error": {"message": "no credits"}}')

    _patch_send(monkeypatch, responder)
    payload = b'{"model": "x", "messages": []}'
    response = client.post(
        "/or/v1/chat/completions",
        content=payload,
        headers={
            "Authorization": "Bearer sk-test",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 402
    assert response.json()["error"]["message"] == "no credits"
    assert seen["body"] == payload


def test_proxy_stream_content_type_preserved(monkeypatch, client):
    sse = b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'

    def responder(request):
        return FakeUpstream(200, sse, content_type="text/event-stream")

    _patch_send(monkeypatch, responder)
    with client.stream(
        "POST",
        "/or/v1/chat/completions",
        content=b"{}",
        headers={"Authorization": "Bearer sk-test"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = b"".join(response.iter_bytes())
    assert b'"content": "hi"' in body


def test_proxy_upstream_unreachable(monkeypatch, client):
    import app.routers.ai_proxy as mod

    class BoomClient(httpx.AsyncClient):
        async def send(self, request, stream=False):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(mod.httpx, "AsyncClient", BoomClient)
    response = client.get("/or/v1/models", headers={"Authorization": "Bearer sk"})
    assert response.status_code == 502
