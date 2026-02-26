"""Content extraction orchestrator with multi-tier fallbacks.

Tier 1: trafilatura (fast, local, good for static HTML)
Tier 2: Jina.ai Reader (free API, great for JS-heavy sites and paywalls)
Tier 3: Basic HTML stripping (last resort)
"""

from __future__ import annotations

import logging
import re

import httpx
import trafilatura

from company_research.models import ScrapedPage
from company_research.scrape.http_scraper import fetch_url

logger = logging.getLogger(__name__)

# Keywords for content quality scoring
QUALITY_KEYWORDS = [
    "private credit", "direct lending", "middle market", "unitranche",
    "first lien", "senior secured", "portfolio", "fund", "aum",
    "credit facility", "leveraged", "mezzanine", "private debt",
    "credit agreement", "covenant", "loan", "borrower", "lender",
    "investment", "capital", "billion", "million",
]


async def extract_page(
    url: str,
    title: str = "",
    company_name: str = "",
    timeout: int = 30,
    max_chars: int = 15000,
) -> ScrapedPage:
    """Fetch and extract content from a URL.

    Multi-tier extraction:
    1. trafilatura (fast, local HTML extraction)
    2. Jina.ai Reader (free API for JS-heavy / paywall sites)
    3. Basic HTML stripping (last resort)
    """
    content = None

    # Tier 1: Direct fetch + trafilatura
    html, error = await fetch_url(url, timeout=timeout)
    if html:
        content = trafilatura.extract(
            html,
            include_tables=True,
            include_links=False,
            include_comments=False,
            favor_recall=True,
            url=url,
        )
        # Fallback to basic extraction if trafilatura returns too little
        if not content or len(content) < 100:
            content = _basic_html_to_text(html)

    # Tier 2: Jina.ai Reader (free, handles JS-rendered pages)
    if not content or len(content) < 100:
        jina_content = await _fetch_via_jina(url, timeout=timeout)
        if jina_content and len(jina_content) > len(content or ""):
            content = jina_content

    if not content or len(content) < 50:
        return ScrapedPage(
            url=url,
            title=title,
            error=error or "no_extractable_content",
        )

    # Truncate at sentence boundary
    content = _truncate_content(content, max_chars)
    quality = _score_content_quality(content, company_name)

    return ScrapedPage(
        url=url,
        title=title,
        content=content,
        content_length=len(content),
        quality_score=quality,
    )


async def _fetch_via_jina(url: str, timeout: int = 30) -> str | None:
    """Fetch clean markdown via Jina.ai Reader API (free, no API key).

    Jina renders JavaScript and returns clean markdown — perfect for
    JS-heavy sites, SPAs, and pages behind cookie walls.
    """
    jina_url = f"https://r.jina.ai/{url}"
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            response = await client.get(
                jina_url,
                headers={
                    "Accept": "text/plain",
                    "X-Return-Format": "text",
                },
            )
            if response.status_code == 200 and len(response.text) > 100:
                return response.text
    except Exception as e:
        logger.debug("Jina.ai fallback failed for %s: %s", url[:60], e)
    return None


def _basic_html_to_text(html: str) -> str:
    """Fallback HTML-to-text when trafilatura fails.

    Similar to the n8n workflow's regex approach but slightly improved.
    """
    text = html
    # Remove scripts and styles
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", "", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", "", text, flags=re.I | re.S)
    # Remove HTML comments
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    # Remove all tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = re.sub(r"&[a-z]+;", " ", text, flags=re.I)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _truncate_content(text: str, max_chars: int) -> str:
    """Truncate at sentence boundary if over max length."""
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    # Try to cut at a sentence boundary
    last_period = truncated.rfind(". ")
    last_newline = truncated.rfind("\n")
    cut_point = max(last_period, last_newline)

    if cut_point > max_chars * 0.8:
        return truncated[:cut_point + 1] + " [content truncated]"
    return truncated + "... [content truncated]"


def _score_content_quality(text: str, company_name: str) -> float:
    """Score extracted content quality 0-100."""
    score = 0.0
    text_lower = text.lower()

    # Length check
    if len(text) < 200:
        return 5.0
    score += min(20.0, len(text) / 500)

    # Company name presence
    if company_name and company_name.lower() in text_lower:
        score += 25.0

    # Private credit keyword hits
    keyword_hits = sum(1 for kw in QUALITY_KEYWORDS if kw in text_lower)
    score += min(30.0, keyword_hits * 4)

    # Financial data indicators
    if re.search(r"\$[\d,.]+\s*(million|billion|M|B|MM)", text, re.I):
        score += 15.0

    # Recency indicators (current or prior year)
    from datetime import datetime
    current_year = datetime.now().year
    for year in [current_year, current_year - 1]:
        if str(year) in text:
            score += 10.0
            break

    return min(100.0, score)
