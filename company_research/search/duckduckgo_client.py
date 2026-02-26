"""Free DuckDuckGo search fallback — no API key required."""

from __future__ import annotations

import asyncio
import logging
import time

from ddgs import DDGS

logger = logging.getLogger(__name__)

# Serialise DDG requests: asyncio lock ensures only one request at a time,
# plus a minimum interval between requests to avoid 429s.
_ddg_lock: asyncio.Lock | None = None
_last_request_time: float = 0
_DDG_MIN_INTERVAL = 2.0  # seconds between requests


def _get_lock() -> asyncio.Lock:
    """Get or create the DDG lock for the current event loop."""
    global _ddg_lock
    if _ddg_lock is None:
        _ddg_lock = asyncio.Lock()
    return _ddg_lock


async def search_ddg(
    query: str,
    num_results: int = 10,
) -> dict:
    """Search DuckDuckGo and return results in the same format as Firecrawl.

    All DDG requests are serialised through a lock with a minimum interval
    to avoid overwhelming the free search backends (Google, Brave, Mojeek).

    Returns a normalised dict with:
      - 'organic_results': list of {link, title, snippet, position}
      - 'scraped_content': {} (always empty — DDG doesn't inline-scrape)
    Returns an empty dict with 'error' key on failure.
    """
    global _last_request_time

    lock = _get_lock()
    max_retries = 2

    for attempt in range(max_retries + 1):
        # Serialise: only one DDG request at a time
        async with lock:
            now = time.monotonic()
            elapsed = now - _last_request_time
            if elapsed < _DDG_MIN_INTERVAL:
                await asyncio.sleep(_DDG_MIN_INTERVAL - elapsed)

            try:
                raw = await asyncio.to_thread(
                    _ddg_search_sync, query, num_results,
                )
                _last_request_time = time.monotonic()
            except Exception as e:
                _last_request_time = time.monotonic()
                err_str = str(e)
                if ("429" in err_str or "Too Many" in err_str) and attempt < max_retries:
                    wait = _DDG_MIN_INTERVAL * (attempt + 2)
                    logger.debug(
                        "DDG 429 for '%s', retrying in %.1fs...",
                        query[:40], wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.warning(
                    "DuckDuckGo search error for '%s': %s", query[:80], e,
                )
                return {"error": err_str}

        # Parse results (outside lock — no need to hold it during parsing)
        organic_results = []
        for i, item in enumerate(raw):
            url = item.get("href", "")
            if url:
                organic_results.append({
                    "link": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("body", ""),
                    "position": i + 1,
                })

        return {
            "organic_results": organic_results,
            "scraped_content": {},
        }

    # Should not reach here, but just in case
    return {"error": "max retries exceeded"}


def _ddg_search_sync(query: str, num_results: int) -> list[dict]:
    """Run the synchronous DDG search in a thread."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=num_results))
