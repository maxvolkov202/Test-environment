"""Claude prompt templates for intelligence extraction, company summary, and person extraction."""

from __future__ import annotations

from datetime import datetime

# ---------------------------------------------------------------------------
# PROMPT 1: Structured Data Extraction
# Reworked to prioritize investment strategies, criteria, AUM, founding year,
# and geographic focus.
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are analyzing {company_name} to create a comprehensive intelligence profile for a private credit research tool.

**CRITICAL RULES:**
1. Extract ONLY information explicitly stated in the provided content
2. NEVER infer, assume, or generate information not present in the sources
3. When uncertain, omit the data point — empty fields are better than wrong data
4. Use null for missing singular values, [] for missing arrays, false for missing booleans
5. For recent news: ONLY include items where you can identify a specific event AND at least an approximate timeframe
6. Today's date is {today_date} — prioritize news from the last 12-18 months

**========================================**
**CONTENT TO ANALYZE**
**========================================**

You are analyzing content from {urls_processed} different web pages about {company_name}:

{combined_content}

**========================================**
**REQUIRED OUTPUT STRUCTURE**
**========================================**

Respond with ONLY valid JSON (no markdown, no explanations):

{{
  "companyOverview": {{
    "companyName": null,
    "companyType": null,
    "businessModel": [],
    "assetBackedFocus": false,
    "aum": null,
    "aumType": null,
    "founded": null,
    "employees": null,
    "headquarters": null,
    "officeLocations": [],
    "websiteURL": null
  }},

  "recentActivity": {{
    "acquisitions": [],
    "partnerships": [],
    "fundRaisings": [],
    "majorAnnouncements": [],
    "executiveChanges": []
  }},

  "investmentStrategy": {{
    "lendingTypes": [],
    "facilityStructures": [],
    "dealTypes": [],
    "sponsorTypes": [],
    "syndicationApproach": [],
    "geographicFocus": [],
    "industryFocus": []
  }},

  "investmentCriteria": {{
    "checkSizes": [],
    "dealSizeRanges": [],
    "ebitdaThresholds": [],
    "revenueRequirements": []
  }},

  "portfolioHighlights": {{
    "recentDeals": [],
    "notableCompanies": []
  }}
}}

**========================================**
**CRITICAL FIELDS — SEARCH THOROUGHLY**
**========================================**

These fields are the MOST IMPORTANT. Search ALL provided content carefully for any mention of:

**lendingTypes**: What types of credit do they provide? Look for mentions of:
  First Lien, Unitranche, Second Lien, Mezzanine, Senior Secured, Subordinated, PIK, NAV Financing, Asset-Based Lending, Revolving Credit, Stretch Senior, Split Lien
  Also check for: "we provide...", "our lending solutions", "credit strategies", "investment strategies include"

**facilityStructures**: What structures do they use?
  Term Loan, Revolver, Delayed Draw, Bridge, Unitranche Facility, Club Deal, Bilateral
  Look for: "facility types", "structures we offer", "financing solutions"

**dealTypes**: What kinds of deals do they do?
  LBO, Buyout, Growth Capital, Refinancing, Recapitalization, Add-On Acquisition, Dividend Recap, M&A Financing, Sponsor Finance, Non-Sponsor
  Look for: "we finance...", "transaction types", "deal types"

**checkSizes**: How much do they lend per deal?
  Look for: "$X million", "hold sizes", "check sizes", "commitment sizes", "we can hold up to"
  Format as: "$10M-$50M", "Up to $300 million", etc.

**ebitdaThresholds**: What size companies do they target?
  Look for: "EBITDA of $X+", "minimum EBITDA", "target EBITDA range"

**geographicFocus**: Where do they invest?
  Look for: "North America", "US", "United States", "Europe", "global", regional mentions

**aum**: Assets under management — look for the MOST RECENT figure.
  PRIORITIZE private credit / direct lending AUM if available. Only use total AUM if no private credit-specific figure is found.
  Format: "$X billion" or "$X million"

**aumType**: Set to "Private Credit" if the aum figure is specific to their private credit / direct lending business.
  Set to "Total" if the figure represents total firm-wide AUM across all strategies.
  This distinction is critical for accurate reporting.

**founded**: Year the company was founded. Look for "founded in", "established", "since YYYY"

**========================================**
**FIELD DEFINITIONS**
**========================================**

**companyType**: PRIMARY business classification based on what the content explicitly says about the company:
  "Direct Lender", "Private Credit Manager", "Private Equity Firm", "Multi-Strategy",
  "BDC", "Asset Manager", "CLO Manager", "Investment Consultant", "Law Firm",
  "Pension Fund", "Insurance Company", or null if unclear.
  Do NOT guess — only classify if the content clearly describes their business

**businessModel**: Array of ALL distinct strategies/platforms mentioned

**aum**: Most recent figure — prefer private credit AUM over total AUM
**aumType**: "Private Credit" or "Total" — indicates what the aum figure represents

**Recent Activity**: Each item MUST include timeframe. Format: "Month Year - Description [Source N]"
  where N is the SOURCE number from the content above that contains this information.

**sponsorTypes**: ["Sponsored", "Private Equity Sponsored", "Non-Sponsored", "Founder-Owned"]

**syndicationApproach**: ["Lead Arranger", "Sole Lender", "Club Deal", "Broadly Syndicated", "Bilateral"]

**recentDeals**: Format: "Company Name - Deal Type - $Amount if stated [Source N]" (max 10)

**notableCompanies**: Just company names (max 20)

**========================================**
**OUTPUT REQUIREMENTS**
**========================================**

1. Respond with ONLY the JSON object (no ```json blocks, no explanations)
2. All string values must use proper quotes
3. Use null for missing singular fields
4. Use [] for missing array fields
5. Prioritize most recent/credible information when conflicts exist

Extract all information now:"""


# ---------------------------------------------------------------------------
# PROMPT 2: Company Summary (replaces strategic analysis)
# Much shorter — just asks for a 3-part factual summary.
# ---------------------------------------------------------------------------

def build_summary_prompt(
    company_name: str,
    intelligence: dict,
) -> str:
    """Build a focused company summary prompt from validated intelligence data."""
    overview = intelligence.get("company_overview", {})
    strategy = intelligence.get("investment_strategy", {})
    criteria = intelligence.get("investment_criteria", {})
    recent = intelligence.get("recent_activity", {})
    portfolio = intelligence.get("portfolio_highlights", {})

    def fmt_list(items: list | None) -> str:
        if not items:
            return "Not identified"
        return ", ".join(items)

    # Build recent activity block
    all_news = []
    for category in ["acquisitions", "partnerships", "fund_raisings", "major_announcements", "executive_changes"]:
        items = recent.get(category, [])
        if items:
            all_news.extend(items)
    news_block = "\n".join(f"  - {item}" for item in all_news) if all_news else "  No recent activity found"

    return f"""Summarize {company_name} for a sales research brief. Use ONLY the validated data below — do not fabricate.

**COMPANY DATA:**
- Name: {overview.get("company_name") or company_name}
- Type: {overview.get("company_type") or "Unknown"}
- Business Model: {fmt_list(overview.get("business_model"))}
- AUM: {overview.get("aum") or "Not found"}
- Founded: {overview.get("founded") or "Not found"}
- HQ: {overview.get("headquarters") or "Not found"}
- Lending Types: {fmt_list(strategy.get("lending_types"))}
- Structures: {fmt_list(strategy.get("facility_structures"))}
- Deal Types: {fmt_list(strategy.get("deal_types"))}
- Check Sizes: {fmt_list(criteria.get("check_sizes"))}
- EBITDA Thresholds: {fmt_list(criteria.get("ebitda_thresholds"))}
- Geography: {fmt_list(strategy.get("geographic_focus"))}
- Industries: {fmt_list(strategy.get("industry_focus"))}
- Recent Deals: {fmt_list((portfolio.get("recent_deals") or [])[:5])}
- Recent News:
{news_block}

**OUTPUT:** Respond with ONLY valid JSON (no markdown):

{{
  "overview": "3-4 sentences: what {company_name} does, their scale, market positioning",
  "credit_focus": "2-3 sentences: their private credit / lending approach, strategies, deal preferences",
  "notable_details": "2-3 sentences: anything else noteworthy — recent activity, growth signals, unique aspects"
}}

Be factual and concise. If data is missing, say so briefly rather than guessing."""


# ---------------------------------------------------------------------------
# PROMPT 3: Person Profile Extraction
# ---------------------------------------------------------------------------

PERSON_EXTRACTION_PROMPT = """Extract professional background information for {person_name} who works at {company_name}.

**CONTENT TO ANALYZE:**

{combined_content}

**RULES:**
1. Extract ONLY information explicitly stated in the content — NEVER invent or guess
2. Do NOT fabricate titles, dates, companies, education, or career details
3. If information is not found, use null or empty arrays — empty is always better than wrong
4. Do NOT generate a bioSummary if you have no real facts about the person — return null instead
5. Do NOT guess titles like "Managing Director" or "Partner" unless explicitly stated in the content

**OUTPUT:** Respond with ONLY valid JSON (no markdown):

{{
  "currentTitle": null,
  "currentCompany": "{company_name}",
  "tenureCurrent": null,
  "linkedinUrl": null,
  "priorExperience": [
    {{
      "firm": "Previous Company Name",
      "title": "Their Title",
      "duration": "YYYY-YYYY (X years)",
      "highlights": ["Notable accomplishment or responsibility"]
    }}
  ],
  "education": [
    {{
      "school": "University Name",
      "degree": "MBA, BS Finance, etc.",
      "graduationYear": "YYYY"
    }}
  ],
  "bioSummary": "2-3 sentence summary of their career trajectory and expertise"
}}

**FIELD NOTES:**
- **currentTitle**: Their current job title at {company_name}
- **tenureCurrent**: How long they've been at {company_name}, e.g. "3 years" or "Since 2019"
- **linkedinUrl**: Their LinkedIn profile URL if found in the content (e.g. https://linkedin.com/in/...)
- **priorExperience**: Previous jobs, most recent first. Include firm name, title, duration, and any notable highlights
- **education**: Schools, degrees, graduation years
- **bioSummary**: Brief professional summary based on the facts found

Extract information for {person_name} now:"""
