"""Async HTTP scraper with browser-like headers and retry logic."""

from __future__ import annotations

import asyncio
import logging
import random

import httpx

logger = logging.getLogger(__name__)

# Rotate through realistic user agents to avoid blocks
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


def _get_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


async def fetch_url(
    url: str, timeout: int = 30, max_retries: int = 2,
) -> tuple[str | None, str | None]:
    """Fetch a URL and return (html_content, error_message).

    Returns (content, None) on success or (None, error_string) on failure.
    Retries on transient errors (429, 503, timeouts).
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                max_redirects=5,
                verify=False,
            ) as client:
                response = await client.get(url, headers=_get_headers())

                if response.status_code in (429, 503) and attempt < max_retries:
                    last_error = f"HTTP {response.status_code} (retrying)"
                    await asyncio.sleep(2 * (attempt + 1))
                    continue

                if response.status_code >= 400:
                    return None, f"HTTP {response.status_code}"

                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    if "application/json" in content_type:
                        return response.text, None
                    return None, f"Non-HTML content: {content_type[:50]}"

                return response.text, None

        except httpx.TimeoutException:
            last_error = "timeout"
            if attempt < max_retries:
                await asyncio.sleep(2 * (attempt + 1))
                continue
        except httpx.TooManyRedirects:
            return None, "too_many_redirects"
        except Exception as e:
            last_error = str(e)[:100]
            if attempt < max_retries:
                await asyncio.sleep(1)
                continue

    return None, last_error
