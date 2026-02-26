"""Algorithmic fit scoring — deterministic, no LLM involvement."""

from __future__ import annotations

import re

from company_research.models import (
    CompanyIntelligence,
    FitScore,
)


def compute_fit_score(intelligence: CompanyIntelligence) -> FitScore:
    """Compute a 0-100 fit score based on extracted company intelligence.

    Four categories, 25 points each:
    - Deal Volume: AUM size, recent deal activity
    - Strategy Complexity: lending types, structures, syndication
    - Growth Trajectory: recent news, fund raises
    - Product Fit: company type, check sizes, ICP alignment
    """
    overview = intelligence.company_overview
    strategy = intelligence.investment_strategy
    criteria = intelligence.investment_criteria
    recent = intelligence.recent_activity
    portfolio = intelligence.portfolio_highlights

    # --- 1. DEAL VOLUME (0-25) ---
    deal_score = 0
    aum_billions = _parse_aum_to_billions(overview.aum)
    if aum_billions is not None:
        if aum_billions >= 10:
            deal_score += 20
        elif aum_billions >= 2:
            deal_score += 15
        elif aum_billions >= 0.5:
            deal_score += 10
        else:
            deal_score += 5

    deal_count = len(portfolio.recent_deals)
    if deal_count >= 5:
        deal_score += 5
    elif deal_count >= 2:
        deal_score += 3
    elif deal_count >= 1:
        deal_score += 1

    deal_volume = min(25, deal_score)

    # --- 2. STRATEGY COMPLEXITY (0-25) ---
    strat_score = 0
    strat_score += min(10, len(strategy.lending_types) * 2)
    strat_score += min(8, len(strategy.facility_structures) * 2)

    syndication = [s.lower() for s in strategy.syndication_approach]
    if any("lead" in s for s in syndication):
        strat_score += 7
    elif any("sole" in s for s in syndication):
        strat_score += 5
    elif any("club" in s for s in syndication):
        strat_score += 4
    elif any("bilateral" in s for s in syndication):
        strat_score += 3

    strategy_complexity = min(25, strat_score)

    # --- 3. GROWTH TRAJECTORY (0-25) ---
    growth_score = 0
    total_news = (
        len(recent.fund_raisings)
        + len(recent.acquisitions)
        + len(recent.partnerships)
        + len(recent.major_announcements)
        + len(recent.executive_changes)
    )

    if total_news >= 4:
        growth_score += 12
    elif total_news >= 2:
        growth_score += 8
    elif total_news >= 1:
        growth_score += 4

    if recent.fund_raisings:
        growth_score += 8
    if recent.executive_changes:
        growth_score += 5

    growth_trajectory = min(25, growth_score)

    # --- 4. PRODUCT FIT (0-25) ---
    fit = 0
    company_type = (overview.company_type or "").lower()

    if "direct lend" in company_type or "private credit" in company_type:
        fit += 15
    elif "bdc" in company_type or "business development" in company_type:
        fit += 12
    elif "clo" in company_type:
        fit += 10
    elif "multi-strategy" in company_type or "multi strategy" in company_type:
        fit += 8
    elif "asset manager" in company_type or "alternative" in company_type:
        fit += 7
    elif "private equity" in company_type:
        fit += 5

    if overview.asset_backed_focus:
        fit = max(0, fit - 3)

    for check_size in criteria.check_sizes:
        low, high = _parse_dollar_range(check_size)
        if low is not None and high is not None:
            if 10 <= low <= 500 or 10 <= high <= 500:
                fit += 10
                break
        elif low is not None and 10 <= low <= 500:
            fit += 8
            break

    product_fit = min(25, max(0, fit))

    # --- TOTAL ---
    total = deal_volume + strategy_complexity + growth_trajectory + product_fit

    if total >= 70:
        rating = "High"
    elif total >= 40:
        rating = "Medium"
    else:
        rating = "Low"

    return FitScore(total=total, rating=rating)


def _parse_aum_to_billions(aum_str: str | None) -> float | None:
    """Parse AUM string to billions. Returns None if unparseable."""
    if not aum_str:
        return None

    text = aum_str.lower().replace(",", "").replace("+", "").strip()

    match = re.search(r"\$?([\d.]+)\s*(billion|billion|b\b|bn\b)", text)
    if match:
        return float(match.group(1))

    match = re.search(r"\$?([\d.]+)\s*(million|m\b|mm\b)", text)
    if match:
        return float(match.group(1)) / 1000

    match = re.search(r"\$?([\d.]+)\s*(trillion|t\b)", text)
    if match:
        return float(match.group(1)) * 1000

    return None


def _parse_dollar_range(text: str) -> tuple[float | None, float | None]:
    """Parse dollar range strings like '$10M-$50M', 'Up to $300 million'.

    Returns (low, high) in millions. Either can be None.
    """
    text = text.lower().replace(",", "").strip()

    def _to_millions(num_str: str, unit_str: str) -> float:
        val = float(num_str)
        if "billion" in unit_str or unit_str in ("b", "bn"):
            return val * 1000
        return val  # assume millions

    # Range: "$10M-$50M" or "$10 million - $50 million"
    range_match = re.search(
        r"\$?([\d.]+)\s*(million|billion|m|mm|b|bn)?\s*[-–to]+\s*\$?([\d.]+)\s*(million|billion|m|mm|b|bn)?",
        text,
    )
    if range_match:
        low_num = range_match.group(1)
        low_unit = range_match.group(2) or "m"
        high_num = range_match.group(3)
        high_unit = range_match.group(4) or low_unit
        return _to_millions(low_num, low_unit), _to_millions(high_num, high_unit)

    # "Up to $300 million"
    up_to_match = re.search(r"up\s+to\s+\$?([\d.]+)\s*(million|billion|m|mm|b|bn)?", text)
    if up_to_match:
        val = _to_millions(up_to_match.group(1), up_to_match.group(2) or "m")
        return 0, val

    # Single value: "$25M+"
    single_match = re.search(r"\$?([\d.]+)\s*(million|billion|m|mm|b|bn)", text)
    if single_match:
        val = _to_millions(single_match.group(1), single_match.group(2))
        return val, val * 5

    return None, None
