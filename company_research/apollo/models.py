"""Pydantic models for Apollo.io API responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ApolloPhone(BaseModel):
    raw_number: str = ""
    sanitized_number: str = ""
    type: str = ""  # mobile, work, etc.


class ApolloEmploymentHistory(BaseModel):
    title: str = ""
    organization_name: str = ""
    start_date: str = ""
    end_date: str = ""
    current: bool = False


class ApolloPerson(BaseModel):
    """A person record from Apollo search or enrichment."""
    id: str = ""
    first_name: str = ""
    last_name: str = ""
    name: str = ""
    title: str = ""
    email: str = ""
    email_status: str = ""  # verified, guessed, unavailable
    linkedin_url: str = ""
    photo_url: str = ""
    phone_numbers: list[ApolloPhone] = Field(default_factory=list)
    organization_name: str = ""
    organization_id: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    employment_history: list[ApolloEmploymentHistory] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    seniority: str = ""  # c_suite, vp, director, manager, etc.

    @property
    def full_name(self) -> str:
        return self.name or f"{self.first_name} {self.last_name}".strip()

    @property
    def primary_phone(self) -> str:
        if self.phone_numbers:
            return self.phone_numbers[0].sanitized_number or self.phone_numbers[0].raw_number
        return ""


class ApolloOrganization(BaseModel):
    """A company/organization record from Apollo."""
    id: str = ""
    name: str = ""
    website_url: str = ""
    linkedin_url: str = ""
    phone: str = ""
    founded_year: int | None = None
    estimated_num_employees: int | None = None
    industry: str = ""
    keywords: list[str] = Field(default_factory=list)
    city: str = ""
    state: str = ""
    country: str = ""
    short_description: str = ""
    annual_revenue: float | None = None
    total_funding: float | None = None


class ApolloSearchResponse(BaseModel):
    """Response from Apollo people/org search endpoints."""
    people: list[ApolloPerson] = Field(default_factory=list)
    organizations: list[ApolloOrganization] = Field(default_factory=list)
    pagination: dict = Field(default_factory=dict)

    @property
    def total_entries(self) -> int:
        return self.pagination.get("total_entries", 0)

    @property
    def page(self) -> int:
        return self.pagination.get("page", 1)

    @property
    def per_page(self) -> int:
        return self.pagination.get("per_page", 25)
