"""Apollo.io search and enrichment routes."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from company_research.apollo.client import ApolloClient
from company_research.web.deps import get_config, get_db

router = APIRouter(prefix="/apollo", tags=["apollo"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _get_apollo() -> ApolloClient:
    cfg = get_config()
    return ApolloClient(api_key=cfg.apollo_api_key)


@router.get("/search", response_class=HTMLResponse)
async def apollo_search_page(request: Request):
    cfg = get_config()
    return templates.TemplateResponse("apollo_search.html", {
        "request": request,
        "configured": bool(cfg.apollo_api_key),
        "results": None,
        "search_type": "people",
        "query_params": {},
    })


@router.post("/search/people", response_class=HTMLResponse)
async def search_people(
    request: Request,
    titles: str = Form(""),
    company: str = Form(""),
    locations: str = Form(""),
    seniorities: str = Form(""),
    page: int = Form(1),
):
    apollo = _get_apollo()
    if not apollo.is_configured:
        return templates.TemplateResponse("apollo_search.html", {
            "request": request,
            "configured": False,
            "results": None,
            "search_type": "people",
            "query_params": {},
            "error": "Apollo API key not configured. Set APOLLO_API_KEY in .env",
        })

    title_list = [t.strip() for t in titles.split(",") if t.strip()] or None
    location_list = [l.strip() for l in locations.split(",") if l.strip()] or None
    seniority_list = [s.strip() for s in seniorities.split(",") if s.strip()] or None

    response = await apollo.search_people(
        q_person_title=title_list,
        q_organization_name=company,
        person_locations=location_list,
        person_seniorities=seniority_list,
        page=page,
    )
    await apollo.close()

    return templates.TemplateResponse("apollo_search.html", {
        "request": request,
        "configured": True,
        "results": response,
        "search_type": "people",
        "query_params": {
            "titles": titles, "company": company,
            "locations": locations, "seniorities": seniorities,
        },
    })


@router.post("/search/organizations", response_class=HTMLResponse)
async def search_organizations(
    request: Request,
    name: str = Form(""),
    locations: str = Form(""),
    keywords: str = Form(""),
    employees: str = Form(""),
):
    apollo = _get_apollo()
    if not apollo.is_configured:
        return templates.TemplateResponse("apollo_search.html", {
            "request": request,
            "configured": False,
            "results": None,
            "search_type": "organizations",
            "query_params": {},
            "error": "Apollo API key not configured. Set APOLLO_API_KEY in .env",
        })

    location_list = [l.strip() for l in locations.split(",") if l.strip()] or None
    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()] or None
    employee_ranges = [employees] if employees else None

    response = await apollo.search_organizations(
        q_organization_name=name,
        organization_locations=location_list,
        q_organization_keyword_tags=keyword_list,
        organization_num_employees_ranges=employee_ranges,
    )
    await apollo.close()

    return templates.TemplateResponse("apollo_search.html", {
        "request": request,
        "configured": True,
        "results": response,
        "search_type": "organizations",
        "query_params": {"name": name, "locations": locations, "keywords": keywords, "employees": employees},
    })


@router.post("/import-people")
async def import_people(
    selected_ids: str = Form(""),
    people_json: str = Form(""),
):
    """Import selected Apollo people as prospects."""
    db = get_db()
    now = datetime.now().isoformat()

    people_data = json.loads(people_json) if people_json else []
    selected = set(selected_ids.split(",")) if selected_ids else set()

    imported = 0
    for person in people_data:
        if person.get("id") not in selected:
            continue
        db.insert(
            "INSERT INTO prospects (name, email, title, company_name, linkedin_url, phone, "
            "apollo_id, apollo_data_json, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'apollo', ?, ?)",
            (
                person.get("name", ""),
                person.get("email", ""),
                person.get("title", ""),
                person.get("organization_name", ""),
                person.get("linkedin_url", ""),
                person.get("primary_phone", ""),
                person.get("id", ""),
                json.dumps(person),
                now, now,
            ),
        )
        imported += 1

    return RedirectResponse(f"/prospects?imported={imported}", status_code=303)


@router.post("/enrich/{prospect_id}")
async def enrich_prospect(prospect_id: int):
    """Enrich a prospect with Apollo data."""
    db = get_db()
    prospect = db.fetchone("SELECT * FROM prospects WHERE id = ?", (prospect_id,))
    if not prospect:
        return {"error": "Prospect not found"}

    apollo = _get_apollo()
    if not apollo.is_configured:
        return {"error": "Apollo API key not configured"}

    person = await apollo.enrich_person(
        email=prospect["email"],
        first_name=prospect["name"].split()[0] if prospect["name"] else "",
        last_name=prospect["name"].split()[-1] if prospect["name"] and " " in prospect["name"] else "",
        organization_name=prospect["company_name"],
        linkedin_url=prospect["linkedin_url"],
    )
    await apollo.close()

    if not person:
        return {"error": "No Apollo match found"}

    now = datetime.now().isoformat()
    updates = {
        "apollo_id": person.id,
        "apollo_data_json": person.model_dump_json(),
    }
    if person.email and not prospect["email"]:
        updates["email"] = person.email
    if person.title and not prospect["title"]:
        updates["title"] = person.title
    if person.linkedin_url and not prospect["linkedin_url"]:
        updates["linkedin_url"] = person.linkedin_url
    if person.primary_phone and not prospect["phone"]:
        updates["phone"] = person.primary_phone

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [now, prospect_id]
    db.update(
        f"UPDATE prospects SET {set_clause}, updated_at = ? WHERE id = ?",
        tuple(params),
    )

    return {"success": True, "enriched_fields": list(updates.keys())}
