"""Async Firecrawl search client with integrated scraping."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"


async def search_firecrawl(
    query: str,
    api_key: str,
    num_results: int = 10,
    timeout: int = 60,
) -> dict:
    """Execute a Google search via Firecrawl with inline page scraping.

    Requests markdown content for each result so the pipeline can skip
    the separate trafilatura scrape step.

    Returns a normalised dict with:
      - 'organic_results': list of {link, title, snippet, position}
      - 'scraped_content': dict mapping URL -> markdown text
    Returns an empty dict with 'error' key on failure.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "limit": num_results,
        "scrapeOptions": {
            "formats": ["markdown"],
            "onlyMainContent": True,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                FIRECRAWL_SEARCH_URL,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("success", False):
                warning = data.get("warning", "unknown error")
                logger.warning("Firecrawl search failed for query '%s': %s", query[:80], warning)
                return {"error": warning}

            # Normalise Firecrawl v2 response (data nested by source type)
            raw_data = data.get("data", {})
            # v2 nests under "web"; fall back to flat list for v1 compat
            if isinstance(raw_data, list):
                raw_results = raw_data
            else:
                raw_results = raw_data.get("web", [])

            organic_results = []
            scraped_content: dict[str, str] = {}

            for i, item in enumerate(raw_results):
                url = item.get("url", "")
                organic_results.append({
                    "link": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("description", ""),
                    "position": i + 1,
                })

                # Capture markdown content if Firecrawl returned it
                markdown = item.get("markdown", "")
                if url and markdown and len(markdown) > 50:
                    scraped_content[url] = markdown

            return {
                "organic_results": organic_results,
                "scraped_content": scraped_content,
            }

    except httpx.TimeoutException:
        logger.warning("Firecrawl timeout for query: %s", query[:80])
        return {"error": "timeout"}
    except httpx.HTTPStatusError as e:
        logger.warning("Firecrawl HTTP %d for query: %s", e.response.status_code, query[:80])
        return {"error": f"http_{e.response.status_code}"}
    except Exception as e:
        logger.warning("Firecrawl error for query '%s': %s", query[:80], e)
        return {"error": str(e)}
