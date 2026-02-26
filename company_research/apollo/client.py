"""Apollo.io API client — async people/company search and enrichment."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from company_research.apollo.models import (
    ApolloOrganization,
    ApolloPerson,
    ApolloPhone,
    ApolloEmploymentHistory,
    ApolloSearchResponse,
)

logger = logging.getLogger(__name__)

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"


class ApolloClient:
    """Async client for Apollo.io REST API."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._sem = asyncio.Semaphore(3)  # Rate limit: 3 concurrent requests
        self._client: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=APOLLO_BASE_URL,
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _post(self, endpoint: str, payload: dict) -> dict:
        """Make an authenticated POST request to Apollo API."""
        if not self.api_key:
            return {}
        async with self._sem:
            client = await self._get_client()
            payload["api_key"] = self.api_key
            try:
                r = await client.post(endpoint, json=payload)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                logger.warning("Apollo API error %s for %s: %s", e.response.status_code, endpoint, e)
                return {}
            except Exception as e:
                logger.warning("Apollo request failed for %s: %s", endpoint, e)
                return {}

    async def search_people(
        self,
        *,
        q_person_title: list[str] | None = None,
        q_organization_name: str = "",
        person_locations: list[str] | None = None,
        person_seniorities: list[str] | None = None,
        organization_industry_tag_ids: list[str] | None = None,
        organization_num_employees_ranges: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> ApolloSearchResponse:
        """Search for people using Apollo's mixed_people/search endpoint."""
        payload: dict[str, Any] = {"page": page, "per_page": per_page}

        if q_person_title:
            payload["person_titles"] = q_person_title
        if q_organization_name:
            payload["q_organization_name"] = q_organization_name
        if person_locations:
            payload["person_locations"] = person_locations
        if person_seniorities:
            payload["person_seniorities"] = person_seniorities
        if organization_industry_tag_ids:
            payload["organization_industry_tag_ids"] = organization_industry_tag_ids
        if organization_num_employees_ranges:
            payload["organization_num_employees_ranges"] = organization_num_employees_ranges

        data = await self._post("/mixed_people/search", payload)
        return self._parse_people_response(data)

    async def search_organizations(
        self,
        *,
        q_organization_name: str = "",
        organization_industry_tag_ids: list[str] | None = None,
        organization_num_employees_ranges: list[str] | None = None,
        organization_locations: list[str] | None = None,
        q_organization_keyword_tags: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> ApolloSearchResponse:
        """Search for organizations using Apollo's mixed_companies/search endpoint."""
        payload: dict[str, Any] = {"page": page, "per_page": per_page}

        if q_organization_name:
            payload["q_organization_name"] = q_organization_name
        if organization_industry_tag_ids:
            payload["organization_industry_tag_ids"] = organization_industry_tag_ids
        if organization_num_employees_ranges:
            payload["organization_num_employees_ranges"] = organization_num_employees_ranges
        if organization_locations:
            payload["organization_locations"] = organization_locations
        if q_organization_keyword_tags:
            payload["q_organization_keyword_tags"] = q_organization_keyword_tags

        data = await self._post("/mixed_companies/search", payload)
        return self._parse_org_response(data)

    async def enrich_person(
        self,
        *,
        email: str = "",
        first_name: str = "",
        last_name: str = "",
        organization_name: str = "",
        linkedin_url: str = "",
    ) -> ApolloPerson | None:
        """Enrich/match a person to get verified email, phone, work history."""
        payload: dict[str, Any] = {}

        if email:
            payload["email"] = email
        if first_name:
            payload["first_name"] = first_name
        if last_name:
            payload["last_name"] = last_name
        if organization_name:
            payload["organization_name"] = organization_name
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url

        if not payload:
            return None

        data = await self._post("/people/match", payload)
        person_data = data.get("person")
        if not person_data:
            return None
        return self._parse_person(person_data)

    def _parse_people_response(self, data: dict) -> ApolloSearchResponse:
        people = [self._parse_person(p) for p in data.get("people", []) if p]
        return ApolloSearchResponse(
            people=people,
            pagination=data.get("pagination", {}),
        )

    def _parse_org_response(self, data: dict) -> ApolloSearchResponse:
        orgs = [self._parse_organization(o) for o in data.get("organizations", []) if o]
        return ApolloSearchResponse(
            organizations=orgs,
            pagination=data.get("pagination", {}),
        )

    def _parse_person(self, p: dict) -> ApolloPerson:
        phones = []
        for ph in p.get("phone_numbers", []) or []:
            phones.append(ApolloPhone(
                raw_number=ph.get("raw_number", ""),
                sanitized_number=ph.get("sanitized_number", ""),
                type=ph.get("type", ""),
            ))

        employment = []
        for eh in p.get("employment_history", []) or []:
            employment.append(ApolloEmploymentHistory(
                title=eh.get("title", ""),
                organization_name=eh.get("organization_name", ""),
                start_date=eh.get("start_date", ""),
                end_date=eh.get("end_date", ""),
                current=eh.get("current", False),
            ))

        org = p.get("organization") or {}

        return ApolloPerson(
            id=p.get("id", ""),
            first_name=p.get("first_name", ""),
            last_name=p.get("last_name", ""),
            name=p.get("name", ""),
            title=p.get("title", ""),
            email=p.get("email", ""),
            email_status=p.get("email_status", ""),
            linkedin_url=p.get("linkedin_url", ""),
            photo_url=p.get("photo_url", ""),
            phone_numbers=phones,
            organization_name=org.get("name", "") or p.get("organization_name", ""),
            organization_id=org.get("id", ""),
            city=p.get("city", ""),
            state=p.get("state", ""),
            country=p.get("country", ""),
            employment_history=employment,
            departments=p.get("departments", []) or [],
            seniority=p.get("seniority", ""),
        )

    def _parse_organization(self, o: dict) -> ApolloOrganization:
        return ApolloOrganization(
            id=o.get("id", ""),
            name=o.get("name", ""),
            website_url=o.get("website_url", ""),
            linkedin_url=o.get("linkedin_url", ""),
            phone=o.get("phone", ""),
            founded_year=o.get("founded_year"),
            estimated_num_employees=o.get("estimated_num_employees"),
            industry=o.get("industry", ""),
            keywords=o.get("keywords", []) or [],
            city=o.get("city", ""),
            state=o.get("state", ""),
            country=o.get("country", ""),
            short_description=o.get("short_description", ""),
            annual_revenue=o.get("annual_revenue"),
            total_funding=o.get("total_funding"),
        )
