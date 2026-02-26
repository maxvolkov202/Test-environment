"""Multi-query search strategy for comprehensive company intelligence."""

from __future__ import annotations

import re


def generate_queries(search_name: str, max_queries: int = 6) -> list[dict]:
    """Generate multiple targeted search queries for a company.

    Each query targets a different aspect of company intelligence.
    Returns list of dicts with 'query' and 'purpose' keys.
    """
    likely_domain = _guess_domain(search_name)

    queries = [
        {
            "query": f'"{search_name}" private credit direct lending',
            "purpose": "core_strategy",
        },
        {
            "query": (
                f'"{search_name}" site:{likely_domain} '
                f'credit OR lending OR "direct lending"'
            ),
            "purpose": "company_site_credit",
        },
        {
            "query": (
                f'"{search_name}" AUM OR "assets under management" '
                f'OR fund OR fundraise'
            ),
            "purpose": "fund_activity",
        },
        {
            "query": (
                f'"{search_name}" unitranche OR "first lien" '
                f'OR "senior secured" OR mezzanine'
            ),
            "purpose": "deal_structure",
        },
        {
            "query": (
                f'"{search_name}" portfolio OR "recent transaction" '
                f'OR deal OR "credit facility"'
            ),
            "purpose": "portfolio_deals",
        },
        {
            "query": (
                f'"{search_name}" founded OR history '
                f'OR "about us" OR team'
            ),
            "purpose": "about_team",
        },
    ]

    return queries[:max_queries]


def generate_person_queries(
    person_name: str,
    company_name: str,
    company_domain: str | None = None,
) -> list[dict]:
    """Generate search queries for a specific person at a company.

    Kept to 2 queries max to reduce DDG rate-limit pressure.
    LinkedIn site-search is skipped because DDG backends block it.
    """
    queries = []

    # Primary query: person + company (most effective single query)
    queries.append({
        "query": f'"{person_name}" "{company_name}"',
        "purpose": "person_at_company",
    })

    # Site-specific search (finds bio pages directly on company website)
    if company_domain:
        queries.append({
            "query": f'site:{company_domain} "{person_name}"',
            "purpose": "person_company_site",
        })
    else:
        # Fall back to industry-specific search
        queries.append({
            "query": f'"{person_name}" private credit OR direct lending',
            "purpose": "person_industry",
        })

    return queries


def generate_team_page_query(company_domain: str) -> dict:
    """Generate a search query to find the company's team/professionals page."""
    return {
        "query": (
            f'site:{company_domain} '
            f'team OR professionals OR people OR "our team" OR leadership'
        ),
        "purpose": "team_directory",
    }


def _guess_domain(search_name: str) -> str:
    """Guess the company's likely domain from its name.

    e.g. "Golub Capital" -> "golubcapital.com"
         "Blackstone Credit (fka GSO)" -> "blackstone.com"
         "PGIM Inc" -> "pgim.com"
    """
    cleaned = search_name
    # Strip parenthetical aliases like "(fka GSO)" or "(NEPC)"
    cleaned = re.sub(r'\s*\([^)]*\)', '', cleaned)
    # Strip "L.P." / "L.L.C." style abbreviations
    cleaned = re.sub(r',?\s*L\.?P\.?$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r',?\s*L\.?L\.?C\.?$', '', cleaned, flags=re.IGNORECASE)
    # Strip legal/business suffixes
    cleaned = re.sub(
        r'\b(Inc|LLC|LP|Ltd|Corp|Group|Holdings|Partners|'
        r'Capital|Credit|Management|Advisors|Advisory|'
        r'Asset Management|Investments|Investment)\b',
        '', cleaned, flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", cleaned).lower()
    if not cleaned:
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", search_name).lower()
    return f"{cleaned}.com"
