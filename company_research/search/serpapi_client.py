"""Async SerpAPI client with rate limiting."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

SERPAPI_BASE_URL = "https://serpapi.com/search.json"


async def search_google(
    query: str,
    api_key: str,
    num_results: int = 10,
    timeout: int = 20,
) -> dict:
    """Execute a Google search via SerpAPI.

    Returns the full JSON response dict.
    Returns an empty dict with 'error' key on failure.
    """
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": str(num_results),
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(SERPAPI_BASE_URL, params=params)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        logger.warning("SerpAPI timeout for query: %s", query[:80])
        return {"error": "timeout"}
    except httpx.HTTPStatusError as e:
        logger.warning("SerpAPI HTTP %d for query: %s", e.response.status_code, query[:80])
        return {"error": f"http_{e.response.status_code}"}
    except Exception as e:
        logger.warning("SerpAPI error for query '%s': %s", query[:80], e)
        return {"error": str(e)}
