"""Structured intelligence extraction from scraped content via Claude."""

from __future__ import annotations

import json
import logging
import re

from company_research.analysis.llm_client import llm_complete
from company_research.analysis.prompts import EXTRACTION_PROMPT
from company_research.config import Config
from company_research.models import (
    CompanyIntelligence,
    CompanyOverview,
    InvestmentCriteria,
    InvestmentStrategy,
    PortfolioHighlights,
    RecentActivity,
    ScrapedPage,
)

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """Extract the first valid JSON object from Claude's response."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        return cleaned

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        c = cleaned[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start:i + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    break

    return cleaned


def build_combined_content(pages: list[ScrapedPage]) -> tuple[str, int]:
    """Combine scraped page contents into a single document for Claude.

    Returns (combined_text, valid_url_count).
    """
    sections = []
    valid_count = 0

    for i, page in enumerate(pages):
        if not page.content:
            continue
        valid_count += 1
        sections.append(
            f"\n{'=' * 80}\n"
            f"SOURCE {valid_count} of {len(pages)}\n"
            f"URL: {page.url}\n"
            f"PAGE TITLE: {page.title}\n"
            f"{'=' * 80}\n\n"
            f"{page.content}\n"
        )

    return "\n".join(sections), valid_count


async def extract_company_intelligence(
    company_name: str,
    pages: list[ScrapedPage],
    config: Config,
) -> CompanyIntelligence:
    """Call Claude to extract structured intelligence from scraped content.

    Returns a validated CompanyIntelligence object.
    Falls back to empty defaults on any failure.
    """
    combined_content, urls_processed = build_combined_content(pages)

    if urls_processed == 0:
        logger.warning("No usable content for %s — returning empty intelligence", company_name)
        return CompanyIntelligence()

    from datetime import datetime

    prompt = EXTRACTION_PROMPT.format(
        company_name=company_name,
        today_date=datetime.now().strftime("%B %d, %Y"),
        urls_processed=urls_processed,
        combined_content=combined_content,
    )

    try:
        response_text = await llm_complete(
            prompt=prompt,
            api_key_anthropic=config.anthropic_api_key,
            api_key_openai=config.openai_api_key,
            model_anthropic=config.extraction_model,
            model_openai=config.openai_extraction_model,
            max_tokens=config.extraction_max_tokens,
            temperature=0,
        )

        parsed = _parse_extraction_response(response_text)
        return parsed

    except Exception as e:
        logger.error("Extraction failed for %s: %s", company_name, e)
        return CompanyIntelligence()


def _parse_extraction_response(response_text: str) -> CompanyIntelligence:
    """Parse Claude's JSON response into a validated CompanyIntelligence object."""
    cleaned = _extract_json(response_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("JSON parse error: %s\nResponse preview: %s", e, cleaned[:200])
        return CompanyIntelligence()

    # Map camelCase JSON keys to snake_case Pydantic models
    try:
        overview_data = data.get("companyOverview", {})
        overview = CompanyOverview(
            company_name=overview_data.get("companyName"),
            company_type=overview_data.get("companyType"),
            business_model=overview_data.get("businessModel", []),
            asset_backed_focus=overview_data.get("assetBackedFocus", False),
            aum=overview_data.get("aum"),
            aum_type=overview_data.get("aumType"),
            founded=overview_data.get("founded"),
            employees=overview_data.get("employees"),
            headquarters=overview_data.get("headquarters"),
            office_locations=overview_data.get("officeLocations", []),
            website_url=overview_data.get("websiteURL"),
        )

        recent_data = data.get("recentActivity", {})
        recent = RecentActivity(
            acquisitions=recent_data.get("acquisitions", []),
            partnerships=recent_data.get("partnerships", []),
            fund_raisings=recent_data.get("fundRaisings", []),
            major_announcements=recent_data.get("majorAnnouncements", []),
            executive_changes=recent_data.get("executiveChanges", []),
        )

        strategy_data = data.get("investmentStrategy", {})
        strategy = InvestmentStrategy(
            lending_types=strategy_data.get("lendingTypes", []),
            facility_structures=strategy_data.get("facilityStructures", []),
            deal_types=strategy_data.get("dealTypes", []),
            sponsor_types=strategy_data.get("sponsorTypes", []),
            syndication_approach=strategy_data.get("syndicationApproach", []),
            geographic_focus=strategy_data.get("geographicFocus", []),
            industry_focus=strategy_data.get("industryFocus", []),
        )

        criteria_data = data.get("investmentCriteria", {})
        criteria_obj = InvestmentCriteria(
            check_sizes=criteria_data.get("checkSizes", []),
            deal_size_ranges=criteria_data.get("dealSizeRanges", []),
            ebitda_thresholds=criteria_data.get("ebitdaThresholds", []),
            revenue_requirements=criteria_data.get("revenueRequirements", []),
        )

        portfolio_data = data.get("portfolioHighlights", {})
        portfolio = PortfolioHighlights(
            recent_deals=portfolio_data.get("recentDeals", []),
            notable_companies=portfolio_data.get("notableCompanies", []),
        )

        return CompanyIntelligence(
            company_overview=overview,
            recent_activity=recent,
            investment_strategy=strategy,
            investment_criteria=criteria_obj,
            portfolio_highlights=portfolio,
        )

    except Exception as e:
        logger.error("Error mapping extraction data: %s", e)
        return CompanyIntelligence()
