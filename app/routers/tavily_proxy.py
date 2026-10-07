"""Tavily passthrough proxy.

Same story as the OpenRouter proxy (`/or`): Tavily blocks RU IPs with HTTP
403, so the app's direct `POST https://api.tavily.com/search` fails on
phones in sanctioned regions. This endpoint forwards the request verbatim
to Tavily from the server's non-RU egress: `POST /tv/search` here becomes
`POST https://api.tavily.com/search` upstream.

Design notes:
  - The caller's own Tavily key rides in the JSON body (`api_key` field) or
    an `Authorization` header, exactly as the Tavily API accepts it; it is
    forwarded untouched.
  - Requests with neither are refused, so this cannot be used as an
    anonymous relay.
  - Tavily responses are small JSON bodies — no streaming needed here.
"""

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter(prefix="/tv")

UPSTREAM = "https://api.tavily.com"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_METHODS: tuple[str, ...] = ("POST", "GET")


@router.api_route("/{path:path}", methods=list(_METHODS))
async def proxy(path: str, request: Request) -> Any:
    auth = request.headers.get("authorization")
    body = await request.body()

    if not auth:
        # Tavily's native style: key inside the JSON body.
        has_body_key = False
        if body:
            try:
                import json

                has_body_key = bool(json.loads(body).get("api_key"))
            except (ValueError, AttributeError):
                has_body_key = False
        if not has_body_key:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "message": "Missing Tavily credentials; this endpoint only forwards your own key (api_key in body or Authorization header)."
                    }
                },
            )

    url = f"{UPSTREAM}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers: dict[str, str] = {}
    if auth:
        headers["Authorization"] = auth
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type

    client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        upstream = await client.post(
            url, headers=headers, content=body or None
        ) if request.method == "POST" else await client.get(url, headers=headers)
    except httpx.HTTPError:
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "Upstream Tavily unreachable"}},
        )
    finally:
        await client.aclose()

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
