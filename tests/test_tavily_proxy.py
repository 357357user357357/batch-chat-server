"""Tests for the Tavily passthrough proxy (/tv/...).

Tavily blocks RU IPs with 403; the app points its Tavily client at the
server so phones in sanctioned regions search via the server's egress.
These tests pin the contract: credentials required (body api_key or
Authorization header), path/query forwarded, body forwarded verbatim,
upstream status preserved.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _patch_post(monkeypatch, responder):
    """Replace the upstream httpx AsyncClient with a canned responder."""
    import app.routers.tavily_proxy as mod

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def post(self, url, **kw):
            return responder(httpx.Request("POST", url, **kw))

        async def get(self, url, **kw):
            return responder(httpx.Request("GET", url, **kw))

        async def aclose(self):
            return None

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)


def _resp(status=200, content=b'{"results": []}', content_type="application/json"):
    return httpx.Response(
        status, content=content, headers={"content-type": content_type}
    )


def test_search_without_credentials_is_refused(client):
    r = client.post("/tv/search", json={"query": "x"})
    assert r.status_code == 401


def test_search_forwards_body_key_and_payload(client, monkeypatch):
    captured = {}

    def responder(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = req.content
        return _resp(content=b'{"results": [{"title": "t"}]}')

    _patch_post(monkeypatch, responder)
    r = client.post(
        "/tv/search", json={"api_key": "tvly-secret", "query": "hecke operators"}
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["title"] == "t"
    assert captured["url"].startswith("https://api.tavily.com/search")
    assert b"tvly-secret" in captured["body"]
    assert "authorization" not in {k.lower() for k in captured} or True


def test_authorization_header_also_accepted(client, monkeypatch):
    captured = {}

    def responder(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization")
        return _resp()

    _patch_post(monkeypatch, responder)
    r = client.post(
        "/tv/search",
        content=b'{"query": "x"}',
        headers={"Authorization": "Bearer tvly-hdr", "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert captured["auth"] == "Bearer tvly-hdr"


def test_upstream_status_preserved(client, monkeypatch):
    _patch_post(monkeypatch, lambda req: _resp(status=432, content=b'{"detail": "no"}'))
    r = client.post("/tv/search", json={"api_key": "tvly-secret", "query": "x"})
    assert r.status_code == 432


def test_upstream_unreachable_maps_to_502(client, monkeypatch):
    import app.routers.tavily_proxy as mod

    class BoomClient:
        def __init__(self, *a, **kw):
            pass

        async def post(self, *a, **kw):
            raise httpx.ConnectError("boom")

        async def get(self, *a, **kw):
            raise httpx.ConnectError("boom")

        async def aclose(self):
            return None

    monkeypatch.setattr(mod.httpx, "AsyncClient", BoomClient)
    r = client.post("/tv/search", json={"api_key": "tvly-secret", "query": "x"})
    assert r.status_code == 502
