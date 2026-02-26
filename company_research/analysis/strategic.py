"""Company summary generation and person profile extraction via Claude."""

from __future__ import annotations

import json
import logging
import re

from company_research.analysis.llm_client import llm_complete
from company_research.analysis.prompts import (
    build_summary_prompt,
    PERSON_EXTRACTION_PROMPT,
)
from company_research.config import Config
from company_research.models import (
    CompanyIntelligence,
    CompanySummary,
    Education,
    PersonProfile,
    ScrapedPage,
    WorkExperience,
)

logger = logging.getLogger(__name__)


async def generate_company_summary(
    company_name: str,
    intelligence: CompanyIntelligence,
    config: Config,
) -> CompanySummary:
    """Call Claude to generate a concise company summary.

    Receives ONLY validated structured data, never raw HTML.
    Returns a CompanySummary.
    """
    intel_dict = json.loads(intelligence.model_dump_json(by_alias=False))
    prompt = build_summary_prompt(company_name, intel_dict)

    try:
        response_text = await llm_complete(
            prompt=prompt,
            api_key_anthropic=config.anthropic_api_key,
            api_key_openai=config.openai_api_key,
            model_anthropic=config.analysis_model,
            model_openai=config.openai_analysis_model,
            max_tokens=2000,
            temperature=0.2,
        )

        return _parse_summary_response(response_text)

    except Exception as e:
        logger.error("Summary generation failed for %s: %s", company_name, e)
        return CompanySummary()


def _parse_summary_response(response_text: str) -> CompanySummary:
    """Parse Claude's JSON response into a CompanySummary."""
    cleaned = _extract_json(response_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Summary JSON parse error: %s", e)
        return CompanySummary(overview=response_text[:500])

    return CompanySummary(
        overview=data.get("overview", ""),
        credit_focus=data.get("credit_focus", ""),
        notable_details=data.get("notable_details", ""),
    )


async def extract_person_profile(
    person_name: str,
    company_name: str,
    pages: list[ScrapedPage],
    config: Config,
) -> PersonProfile:
    """Call Claude to extract a person's professional profile from scraped content."""
    from company_research.analysis.extraction import build_combined_content

    combined_content, urls_processed = build_combined_content(pages)

    if urls_processed == 0:
        logger.warning("No content found for person %s", person_name)
        return PersonProfile(name=person_name, current_company=company_name)

    prompt = PERSON_EXTRACTION_PROMPT.format(
        person_name=person_name,
        company_name=company_name,
        combined_content=combined_content,
    )

    try:
        response_text = await llm_complete(
            prompt=prompt,
            api_key_anthropic=config.anthropic_api_key,
            api_key_openai=config.openai_api_key,
            model_anthropic=config.extraction_model,
            model_openai=config.openai_extraction_model,
            max_tokens=3000,
            temperature=0,
        )

        return _parse_person_response(response_text, person_name, company_name)

    except Exception as e:
        logger.error("Person extraction failed for %s: %s", person_name, e)
        return PersonProfile(name=person_name, current_company=company_name)


def _parse_person_response(
    response_text: str,
    person_name: str,
    company_name: str,
) -> PersonProfile:
    """Parse Claude's JSON response into a PersonProfile."""
    cleaned = _extract_json(response_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Person JSON parse error for %s: %s", person_name, e)
        return PersonProfile(name=person_name, current_company=company_name)

    try:
        prior_exp = []
        for exp in data.get("priorExperience", []):
            if isinstance(exp, dict) and exp.get("firm"):
                prior_exp.append(WorkExperience(
                    firm=exp["firm"],
                    title=exp.get("title", ""),
                    duration=exp.get("duration"),
                    highlights=exp.get("highlights", []),
                ))

        education = []
        for edu in data.get("education", []):
            if isinstance(edu, dict) and edu.get("school"):
                education.append(Education(
                    school=edu["school"],
                    degree=edu.get("degree"),
                    graduation_year=edu.get("graduationYear"),
                ))

        return PersonProfile(
            name=person_name,
            current_title=data.get("currentTitle"),
            current_company=data.get("currentCompany") or company_name,
            tenure_current=data.get("tenureCurrent"),
            prior_experience=prior_exp,
            education=education,
            bio_summary=_clean_bio(data.get("bioSummary")),
            linkedin_url=data.get("linkedinUrl") or "",
        )

    except Exception as e:
        logger.error("Error mapping person data for %s: %s", person_name, e)
        return PersonProfile(name=person_name, current_company=company_name)


def _clean_bio(bio: str | None) -> str | None:
    """Filter out LLM placeholder responses that aren't real bios."""
    if not bio:
        return None
    skip = [
        "no professional background",
        "no information found",
        "not found in the provided",
        "no details available",
        "does not contain any information",
        "could not find",
        "no data available",
        "no biographical",
    ]
    if any(p in bio.lower() for p in skip):
        return None
    return bio


def _extract_json(text: str) -> str:
    """Extract the first valid JSON object from Claude's response.

    Handles code fences, leading/trailing prose, and multiple JSON blocks.
    """
    cleaned = text.strip()

    # Strip code fences
    cleaned = re.sub(r"^```json\s*", "", cleaned)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    # Try direct parse first (fast path)
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    # Find first { and match to its closing }
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
