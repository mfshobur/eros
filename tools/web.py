import urllib.request
import urllib.parse
import urllib.error
import json
from tools.base import BaseTool, register_tool


@register_tool
class WebFetch(BaseTool):
    name = "web_fetch"
    description = "Fetch the text content of a URL. Returns plain text (HTML tags stripped)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to fetch"},
            "max_chars": {"type": "integer", "description": "Max characters to return (default: 8000)"},
        },
        "required": ["url"],
    }

    def execute(self, url: str, max_chars: int = 8000) -> str:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return f"HTTP error {e.code}: {e.reason}"
        except Exception as e:
            return f"Error fetching {url}: {e}"

        text = self._strip_html(raw) if "html" in content_type else raw
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[truncated, {len(text) - max_chars} more chars]"
        return text

    def _strip_html(self, html: str) -> str:
        import re
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<[^>]+>", " ", html)
        html = re.sub(r"&nbsp;", " ", html)
        html = re.sub(r"&amp;", "&", html)
        html = re.sub(r"&lt;", "<", html)
        html = re.sub(r"&gt;", ">", html)
        html = re.sub(r"&quot;", '"', html)
        html = re.sub(r"\s{3,}", "\n\n", html)
        return html.strip()


_SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.bus-hit.me",
    "https://searxng.site",
    "https://searx.tiekoetter.com",
    "https://opnxng.com",
]


@register_tool
class WebSearch(BaseTool):
    name = "web_search"
    description = "Search the web and return top results with titles, snippets, and URLs."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "num_results": {"type": "integer", "description": "Number of results to return (default: 5)"},
        },
        "required": ["query"],
    }

    def execute(self, query: str, num_results: int = 5) -> str:
        # Try SearXNG instances first, fall back to DuckDuckGo
        result = self._searxng(query, num_results)
        if result:
            return result
        return self._ddg(query, num_results)

    def _searxng(self, query: str, num_results: int) -> str | None:
        params = urllib.parse.urlencode({
            "q": query, "format": "json", "language": "en",
            "time_range": "", "safesearch": 0, "categories": "general",
        })
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; eros/1.0)",
            "Accept": "application/json",
        }
        for instance in _SEARXNG_INSTANCES:
            try:
                url = f"{instance}/search?{params}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                hits = data.get("results", [])[:num_results]
                if not hits:
                    continue
                lines = []
                for h in hits:
                    title = h.get("title", "")
                    body = h.get("content", "")[:500]
                    url_ = h.get("url", "")
                    lines.append(f"**{title}**\n{body}\n{url_}")
                return "\n\n".join(lines)
            except Exception:
                continue
        return None

    def _ddg(self, query: str, num_results: int) -> str:
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                hits = list(ddgs.text(query, max_results=num_results))
            if not hits:
                return f"No results found for: {query}"
            lines = []
            for h in hits:
                title = h.get("title", "")
                body = h.get("body", "")[:500]
                url = h.get("href", "")
                lines.append(f"**{title}**\n{body}\n{url}")
            return "\n\n".join(lines)
        except Exception as e:
            return f"Search failed: {e}"
