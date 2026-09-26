"""Client-side web search and fetch tools."""

from __future__ import annotations

from typing import Any

import httpx

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS  # type: ignore[no-redef]


def web_search(arguments: dict[str, Any]) -> str:
    query = arguments["query"]
    max_results = arguments.get("max_results", 5)
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No results found."
        lines = []
        for r in results:
            lines.append(f"**{r.get('title', '')}**")
            lines.append(r.get('href', ''))
            lines.append(r.get('body', '')[:300])
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"Search failed: {e}"


async def web_fetch(arguments: dict[str, Any]) -> str:
    url = arguments["url"]
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            response = await client.get(url, headers={"User-Agent": "my-coding-agent/0.1"})
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" in content_type:
                # Strip tags simply — good enough for most docs pages
                import re
                text = re.sub(r"<style[^>]*>.*?</style>", "", response.text, flags=re.DOTALL)
                text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s{3,}", "\n\n", text)
                return text.strip()[:8000]
            return response.text[:8000]
    except Exception as e:
        return f"Fetch failed: {e}"


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo. Returns titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch the content of a URL and return it as text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
]

HANDLERS: dict[str, Any] = {
    "web_search": web_search,
    "web_fetch": web_fetch,
}
