"""Minimal Tavily web-search client (server side).

The user stores their own Tavily API key in the web Settings modal (persisted
in the server DB). It is used only at runtime for the chat "web search" toggle,
mirroring the Android app's behavior.
"""

import httpx

from app.config import settings
from app.services.provider_errors import ProviderError

TAVILY_URL = "https://api.tavily.com/search"
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def is_configured() -> bool:
    return bool(settings.tavily_api_key)


def search_web(query: str, max_results: int = 3) -> list[dict]:
    """Run a Tavily search and return [{title, url, content}, ...]."""
    if not settings.tavily_api_key:
        raise ProviderError("Tavily API key is not configured on the server")

    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": True,
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(TAVILY_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ProviderError(f"Tavily request failed: {exc}") from exc

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []

    items: list[dict] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or ""
        content = (item.get("content") or item.get("snippet") or "").strip()
        if not (url or content):
            continue
        items.append(
            {
                "title": item.get("title") or "Untitled result",
                "url": url,
                "content": content,
                # Tavily includes this for some pages — helps the model judge
                # how fresh a snippet is instead of trusting it blindly.
                "published_date": item.get("published_date") or "",
            }
        )
    return items


def web_search_context(query: str, results: list[dict]) -> str:
    """Format Tavily results as a compact context block for the system prompt.

    The block is stamped with the search time and the instruction that the
    system prompt's current date/time is authoritative — otherwise models
    treat whatever a page says ("updated Sep 4, 10:02 AM…") as the present.
    """
    from datetime import datetime, timezone

    searched_at = datetime.now(timezone.utc).strftime("%A, %d %B %Y, %H:%M UTC")
    if not results:
        return (
            f'Web search results for "{query}" (searched at {searched_at}): none.'
        )

    lines: list[str] = []
    for index, result in enumerate(results):
        content = " ".join(result["content"].split())
        snippet = content if len(content) <= 280 else content[:280] + "…"
        published = result.get("published_date") or ""
        published_note = f" (page published: {published})" if published else ""
        lines.append(
            f"{index + 1}. {result['title']}{published_note}\n"
            f"   {result['url']}\n"
            f"   {snippet}"
        )
    return (
        f'Web search results for "{query}" (searched at {searched_at} UTC — '
        "snippets may be outdated; use the Current date and time from this "
        "system prompt as 'now', never the times found inside pages):\n\n"
        + "\n\n".join(lines)
    )