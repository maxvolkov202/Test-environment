"""URL quality scoring, deduplication, and ranking."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from company_research.models import RankedURL, SearchResult

# Domain quality scores for private credit research
DOMAIN_SCORES: dict[str, int] = {
    "pitchbook.com": 9,
    "privatedebtinvestor.com": 9,
    "middlemarketgrowth.org": 8,
    "prnewswire.com": 8,
    "businesswire.com": 8,
    "globenewswire.com": 8,
    "linkedin.com": 2,  # Low: login walls block scraping; URLs still captured for buttons
    "reuters.com": 7,
    "bloomberg.com": 7,
    "wsj.com": 7,
    "ft.com": 7,
    "sec.gov": 7,
    "spglobal.com": 7,
    "moodys.com": 6,
    "pehub.com": 7,
    "buyoutsinsider.com": 7,
    "creditflux.com": 8,
    "leveragedloan.com": 8,
}

# Domains to deprioritize
DOMAIN_PENALTIES: dict[str, int] = {
    "facebook.com": -10,
    "instagram.com": -10,
    "twitter.com": -5,
    "x.com": -5,
    "youtube.com": -3,
    "wikipedia.org": -2,
    "glassdoor.com": -8,
    "indeed.com": -8,
    "yelp.com": -10,
    "reddit.com": -3,
    "quora.com": -5,
    "whalewisdom.com": -3,
    "rashmanly.com": -10,
}

# Keywords that signal high relevance for private credit
RELEVANCE_KEYWORDS = [
    "private credit",
    "direct lending",
    "middle market",
    "unitranche",
    "first lien",
    "senior secured",
    "portfolio",
    "fund",
    "aum",
    "credit facility",
    "leveraged",
    "mezzanine",
    "private debt",
    "credit agreement",
    "covenant",
]

# Subpages that are high value on a company's own site
HIGH_VALUE_PATHS = [
    "/credit", "/direct-lending", "/strategies", "/strategy",
    "/team", "/about", "/about-us", "/leadership", "/our-team",
    "/investment", "/investments", "/portfolio", "/funds",
]


def rank_and_deduplicate(
    results: list[SearchResult],
    company_name: str,
    max_urls: int = 12,
) -> list[RankedURL]:
    """Score, deduplicate, and rank search results.

    Returns the top N URLs sorted by quality score.
    """
    # Deduplicate by normalized URL
    seen_urls: dict[str, RankedURL] = {}
    company_slug = _clean_company_name(company_name)

    for result in results:
        url = result.url.strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            continue

        normalized = _normalize_url(url)
        if normalized in seen_urls:
            # Add the query purpose to track which queries found this URL
            existing = seen_urls[normalized]
            if result.query_purpose not in existing.source_queries:
                existing.source_queries.append(result.query_purpose)
                # Boost score for URLs found by multiple queries
                existing.quality_score += 5
            continue

        domain = _extract_domain(url)
        score = _score_url(
            url, result.title, result.snippet, domain,
            result.position, company_slug,
        )

        seen_urls[normalized] = RankedURL(
            url=url,
            title=result.title,
            domain=domain,
            quality_score=score,
            source_queries=[result.query_purpose],
        )

    # Sort by score descending and return top N
    ranked = sorted(seen_urls.values(), key=lambda u: u.quality_score, reverse=True)
    return ranked[:max_urls]


def _score_url(
    url: str,
    title: str,
    snippet: str,
    domain: str,
    position: int,
    company_slug: str,
) -> float:
    """Score a single URL based on domain, content signals, and position."""
    score = 50.0  # Base score

    # Company's own website gets a big boost
    is_company_site = _is_company_domain(domain, company_slug)
    if is_company_site:
        score += 30
        # Bonus for high-value subpages on the company's own site
        path = urlparse(url).path.lower().rstrip("/")
        for hvp in HIGH_VALUE_PATHS:
            if hvp in path:
                score += 10
                break

    # Domain quality bonus/penalty (skip if already boosted as company site)
    if not is_company_site:
        for known_domain, bonus in DOMAIN_SCORES.items():
            if known_domain in domain:
                score += bonus * 3
                break
        else:
            for known_domain, penalty in DOMAIN_PENALTIES.items():
                if known_domain in domain:
                    score += penalty * 3
                    break

    # Title keyword relevance
    combined_text = f"{title} {snippet}".lower()
    keyword_hits = sum(1 for kw in RELEVANCE_KEYWORDS if kw in combined_text)
    score += min(20, keyword_hits * 4)

    # Position in search results (earlier = better)
    if position <= 3:
        score += 10
    elif position <= 6:
        score += 5
    elif position <= 10:
        score += 2

    return score


def _clean_company_name(name: str) -> str:
    """Clean company name to a slug for domain matching.

    e.g. "Golub Capital" -> "golubcapital"
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _is_company_domain(domain: str, company_slug: str) -> bool:
    """Check if a domain likely belongs to the company."""
    if not company_slug or len(company_slug) < 4:
        return False
    # Strip www. and check if the cleaned domain contains the company slug
    clean_domain = domain.replace("www.", "").split(".")[0]
    return company_slug in clean_domain


def _normalize_url(url: str) -> str:
    """Normalize URL for deduplication."""
    parsed = urlparse(url)
    # Remove trailing slashes, fragments, and common tracking params
    path = parsed.path.rstrip("/")
    return f"{parsed.netloc}{path}".lower()


def _extract_domain(url: str) -> str:
    """Extract the domain from a URL."""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""
