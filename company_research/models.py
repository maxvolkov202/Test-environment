"""Pydantic data models for the company research pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class ContactInfo(BaseModel):
    """A single contact with name and optional email."""
    name: str
    email: str = ""
    linkedin_url: str = ""


class CompanyInput(BaseModel):
    """A company to research, with associated contacts."""
    company_name: str
    search_name: str  # Cleaned name (no Inc/LLC suffixes) for search queries
    people: list[str] = Field(default_factory=list)
    contacts: list[ContactInfo] = Field(default_factory=list)  # People with emails


# ---------------------------------------------------------------------------
# Search models
# ---------------------------------------------------------------------------

class SearchResult(BaseModel):
    """A single organic search result from SerpAPI."""
    url: str
    title: str = ""
    snippet: str = ""
    query_purpose: str = ""  # Which query strategy found this
    position: int = 99


class RankedURL(BaseModel):
    """A URL scored and ready for scraping."""
    url: str
    title: str = ""
    domain: str = ""
    quality_score: float = 0.0
    source_queries: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scraping models
# ---------------------------------------------------------------------------

class ScrapedPage(BaseModel):
    """Content extracted from a single URL."""
    url: str
    title: str = ""
    content: str = ""
    content_length: int = 0
    quality_score: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Intelligence extraction models
# ---------------------------------------------------------------------------

class CompanyOverview(BaseModel):
    company_name: str | None = None
    company_type: str | None = None
    business_model: list[str] = Field(default_factory=list)
    asset_backed_focus: bool = False
    aum: str | None = None
    aum_type: str | None = None  # "Private Credit" or "Total"
    founded: str | None = None
    employees: str | None = None
    headquarters: str | None = None
    office_locations: list[str] = Field(default_factory=list)
    website_url: str | None = None

    @field_validator("founded", "employees", "aum", mode="before")
    @classmethod
    def coerce_to_str(cls, v):
        if v is not None:
            return str(v)
        return v


class RecentActivity(BaseModel):
    acquisitions: list[str] = Field(default_factory=list)
    partnerships: list[str] = Field(default_factory=list)
    fund_raisings: list[str] = Field(default_factory=list)
    major_announcements: list[str] = Field(default_factory=list)
    executive_changes: list[str] = Field(default_factory=list)


class InvestmentStrategy(BaseModel):
    lending_types: list[str] = Field(default_factory=list)
    facility_structures: list[str] = Field(default_factory=list)
    deal_types: list[str] = Field(default_factory=list)
    sponsor_types: list[str] = Field(default_factory=list)
    syndication_approach: list[str] = Field(default_factory=list)
    geographic_focus: list[str] = Field(default_factory=list)
    industry_focus: list[str] = Field(default_factory=list)


class InvestmentCriteria(BaseModel):
    check_sizes: list[str] = Field(default_factory=list)
    deal_size_ranges: list[str] = Field(default_factory=list)
    ebitda_thresholds: list[str] = Field(default_factory=list)
    revenue_requirements: list[str] = Field(default_factory=list)


class PortfolioHighlights(BaseModel):
    recent_deals: list[str] = Field(default_factory=list)
    notable_companies: list[str] = Field(default_factory=list)


class CompanyIntelligence(BaseModel):
    """Full structured intelligence extracted from scraped content."""
    company_overview: CompanyOverview = Field(default_factory=CompanyOverview)
    recent_activity: RecentActivity = Field(default_factory=RecentActivity)
    investment_strategy: InvestmentStrategy = Field(default_factory=InvestmentStrategy)
    investment_criteria: InvestmentCriteria = Field(default_factory=InvestmentCriteria)
    portfolio_highlights: PortfolioHighlights = Field(default_factory=PortfolioHighlights)


# ---------------------------------------------------------------------------
# Person intelligence models
# ---------------------------------------------------------------------------

class WorkExperience(BaseModel):
    firm: str
    title: str = ""
    duration: str | None = None  # e.g. "2019-2022 (3 years)"
    highlights: list[str] = Field(default_factory=list)

    @field_validator("title", mode="before")
    @classmethod
    def coerce_title(cls, v):
        if v is None:
            return ""
        return str(v)


class Education(BaseModel):
    school: str
    degree: str | None = None
    graduation_year: str | None = None


class InteractionRecord(BaseModel):
    """A single CRM activity (call, email, meeting)."""
    date: str = ""
    activity_type: str = ""  # Call, Email, Meeting, Task
    subject: str = ""
    notes: str = ""
    owner: str = ""


class PersonProfile(BaseModel):
    name: str
    email: str = ""
    current_title: str | None = None
    current_company: str | None = None
    tenure_current: str | None = None  # e.g. "3 years, 2 months"
    prior_experience: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    bio_summary: str | None = None  # 2-3 sentence summary if available
    linkedin_url: str = ""
    source_urls: list[str] = Field(default_factory=list)  # URLs used for person research
    sf_status: str = ""  # Salesforce lead status
    last_contacted: str = ""  # Last activity date from SF
    interactions: list[InteractionRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Company summary (replaces StrategicAnalysis)
# ---------------------------------------------------------------------------

class CompanySummary(BaseModel):
    """Fact-focused company summary — replaces the old StrategicAnalysis."""
    overview: str = ""          # 3-4 sentences: what they do, scale, positioning
    credit_focus: str = ""      # 2-3 sentences: their private credit / lending approach
    notable_details: str = ""   # 2-3 sentences: anything else noteworthy


# ---------------------------------------------------------------------------
# Fit scoring
# ---------------------------------------------------------------------------

class FitScore(BaseModel):
    total: int = 0
    rating: Literal["High", "Medium", "Low"] = "Low"


# ---------------------------------------------------------------------------
# Salesforce Account / Opportunity / Note models
# ---------------------------------------------------------------------------

class SFOpportunity(BaseModel):
    """A single Salesforce Opportunity."""
    name: str = ""
    stage: str = ""
    amount: str = ""          # formatted string e.g. "$1,500,000"
    close_date: str = ""
    owner: str = ""
    probability: str = ""
    opp_type: str = ""        # "New Business", "Renewal", etc.
    next_step: str = ""
    roadblocks: str = ""      # Custom field: Roadblocks__c
    description: str = ""
    opp_notes: list[str] = Field(default_factory=list)  # Notes linked to this opp


class SFAccountInfo(BaseModel):
    """Salesforce Account-level data including opportunities and notes."""
    account_id: str = ""
    account_name: str = ""
    account_owner: str = ""
    account_type: str = ""    # "Customer", "Prospect", etc.
    industry: str = ""
    last_activity_date: str = ""
    opportunities: list[SFOpportunity] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Final result model
# ---------------------------------------------------------------------------

class CompanyResult(BaseModel):
    """Complete result for one company after the full pipeline."""
    company: CompanyInput
    intelligence: CompanyIntelligence = Field(default_factory=CompanyIntelligence)
    summary: CompanySummary = Field(default_factory=CompanySummary)
    fit_score: FitScore = Field(default_factory=FitScore)
    person_profiles: list[PersonProfile] = Field(default_factory=list)
    sf_account: SFAccountInfo | None = None
    source_urls: list[str] = Field(default_factory=list)  # ordered by source number
    processed_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    from_cache: bool = False
    error: str | None = None

    @classmethod
    def error_result(cls, company: CompanyInput, error_msg: str) -> CompanyResult:
        return cls(company=company, error=error_msg)
