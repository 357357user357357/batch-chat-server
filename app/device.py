"""Device attribution for the sync audit trail.

Every record on the master server remembers WHICH device created, last
modified, or deleted it. Clients may identify themselves explicitly with the
`X-Device-Name` header (the phone app sets this); otherwise a short label is
derived from the User-Agent (web browser / api client / phone app).
"""

from fastapi import Request


def device_label(request: Request) -> str:
    name = (request.headers.get("x-device-name") or "").strip()[:60]
    if name:
        return name
    ua = (request.headers.get("user-agent") or "").lower()
    if "okhttp" in ua or "batch-chat" in ua:
        return "phone-app"
    if "mozilla" in ua:
        return "web"
    if "curl" in ua or "python-requests" in ua or "python-httpx" in ua:
        return "api-client"
    return "unknown"