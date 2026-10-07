"""OpenRouter passthrough proxy.

Phones in sanctioned regions get HTTP 403 straight from OpenRouter (RU IPs
are blocked). The app's OpenRouter traffic therefore rides through this
server (non-RU egress) instead: `POST /or/v1/chat/completions` here is
forwarded verbatim to `https://openrouter.ai/api/v1/chat/completions`,
including SSE streams for live chat.

Design notes:
  - Fully generic passthrough (any path, any method, any body) so the app's
    existing OpenAI-compatible client code works unchanged: the provider
    base_url simply becomes `https://<server>/or/v1`.
  - The caller's `Authorization` header (their own OpenRouter key) is
    forwarded untouched; requests without it are refused, so this cannot be
    used as an anonymous relay.
  - Streaming: the upstream byte stream is piped straight to the client
    (SSE tokens appear in real time; no buffering).
"""

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings

router = APIRouter(prefix="/or")

UPSTREAM = "https://openrouter.ai/api"
# SSE token streams can pause for a long time between chunks ("Waiting for
# model…" / reasoning models); 300s per-read is the same budget the server's
# own OpenRouter client uses for live chat.
CLIENT_TIMEOUT = httpx.Timeout(300.0, connect=15.0)

# Methods we forward (everything an OpenAI-compatible client needs).
_METHODS: tuple[str, ...] = ("GET", "HEAD", "POST", "DELETE", "PUT", "PATCH")


@router.api_route("/{path:path}", methods=list(_METHODS))
async def proxy(path: str, request: Request) -> Any:
    auth = request.headers.get("authorization")
    if not auth:
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Missing Authorization header; this endpoint only forwards your own OpenRouter key."}},
        )

    url = f"{UPSTREAM}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    # Forward a minimal, safe header set: auth + content type. Hop-by-hop and
    # UA-specific headers (host, content-length, accept-encoding, ...) are
    # re-derived by httpx.
    headers = {"Authorization": auth}
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    if request.headers.get("http-referer"):
        headers["HTTP-Referer"] = request.headers["http-referer"]
    title = request.headers.get("x-title")
    if title:
        headers["X-Title"] = title

    body = await request.body()
    client = httpx.AsyncClient(timeout=CLIENT_TIMEOUT)
    try:
        req = client.build_request(
            request.method, url, headers=headers, content=body or None
        )
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "Upstream OpenRouter unreachable"}},
        )

    if upstream.status_code >= 400 or "text/event-stream" not in upstream.headers.get(
        "content-type", ""
    ):
        # Small error/JSON bodies: read fully, then release the connection.
        try:
            content = await upstream.aread()
        finally:
            await upstream.aclose()
        await client.aclose()
        from fastapi.responses import Response

        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    media_type = upstream.headers.get("content-type", "text/event-stream")

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )
