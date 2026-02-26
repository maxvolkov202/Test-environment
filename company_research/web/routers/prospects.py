"""Prospect management — CRUD, CSV import, filtering."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Form, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from company_research.web.deps import get_db

router = APIRouter(prefix="/prospects", tags=["prospects"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("", response_class=HTMLResponse)
async def list_prospects(
    request: Request,
    search: str = "",
    persona_id: str = "",
    company: str = "",
    page: int = 1,
):
    db = get_db()
    per_page = 50
    offset = (page - 1) * per_page

    where_clauses = []
    params: list = []

    if search:
        where_clauses.append("(p.name LIKE ? OR p.email LIKE ? OR p.title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    if persona_id:
        where_clauses.append("p.persona_id = ?")
        params.append(int(persona_id))
    if company:
        where_clauses.append("p.company_name LIKE ?")
        params.append(f"%{company}%")

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    total = db.fetchone(
        f"SELECT COUNT(*) as cnt FROM prospects p {where}", tuple(params)
    )["cnt"]

    prospects = db.fetchall(
        f"SELECT p.*, pe.name as persona_name, pe.color as persona_color "
        f"FROM prospects p LEFT JOIN personas pe ON p.persona_id = pe.id "
        f"{where} ORDER BY p.updated_at DESC LIMIT ? OFFSET ?",
        tuple(params + [per_page, offset]),
    )

    # Get companies for filter dropdown
    companies = db.fetchall(
        "SELECT DISTINCT company_name FROM prospects WHERE company_name != '' ORDER BY company_name"
    )

    # Get personas for filter dropdown
    personas = db.fetchall("SELECT id, name, color FROM personas WHERE is_active = 1 ORDER BY name")

    return templates.TemplateResponse("prospects.html", {
        "request": request,
        "prospects": prospects,
        "total": total,
        "page": page,
        "per_page": per_page,
        "search": search,
        "persona_id": persona_id,
        "company": company,
        "companies": companies,
        "personas": personas,
    })


@router.get("/new", response_class=HTMLResponse)
async def new_prospect_form(request: Request):
    db = get_db()
    personas = db.fetchall("SELECT id, name FROM personas WHERE is_active = 1 ORDER BY name")
    return templates.TemplateResponse("prospect_form.html", {
        "request": request,
        "prospect": None,
        "personas": personas,
    })


@router.post("/new")
async def create_prospect(
    name: str = Form(...),
    email: str = Form(""),
    title: str = Form(""),
    company_name: str = Form(""),
    linkedin_url: str = Form(""),
    phone: str = Form(""),
    persona_id: str = Form(""),
    notes: str = Form(""),
):
    db = get_db()
    now = datetime.now().isoformat()
    db.insert(
        "INSERT INTO prospects (name, email, title, company_name, linkedin_url, phone, "
        "persona_id, notes, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)",
        (name, email, title, company_name, linkedin_url, phone,
         int(persona_id) if persona_id else None, notes, now, now),
    )
    return RedirectResponse("/prospects", status_code=303)


@router.get("/{prospect_id}", response_class=HTMLResponse)
async def prospect_detail(request: Request, prospect_id: int):
    db = get_db()
    prospect = db.fetchone(
        "SELECT p.*, pe.name as persona_name, pe.color as persona_color "
        "FROM prospects p LEFT JOIN personas pe ON p.persona_id = pe.id "
        "WHERE p.id = ?",
        (prospect_id,),
    )
    if not prospect:
        return HTMLResponse("<p>Prospect not found</p>", status_code=404)

    # Get sequence enrollments
    enrollments = db.fetchall(
        "SELECT ps.*, st.name as template_name "
        "FROM prospect_sequences ps "
        "JOIN sequence_templates st ON ps.template_id = st.id "
        "WHERE ps.prospect_id = ? ORDER BY ps.started_at DESC",
        (prospect_id,),
    )

    return templates.TemplateResponse("prospect_detail.html", {
        "request": request,
        "prospect": prospect,
        "enrollments": enrollments,
    })


@router.get("/{prospect_id}/edit", response_class=HTMLResponse)
async def edit_prospect_form(request: Request, prospect_id: int):
    db = get_db()
    prospect = db.fetchone("SELECT * FROM prospects WHERE id = ?", (prospect_id,))
    if not prospect:
        return HTMLResponse("<p>Prospect not found</p>", status_code=404)
    personas = db.fetchall("SELECT id, name FROM personas WHERE is_active = 1 ORDER BY name")
    return templates.TemplateResponse("prospect_form.html", {
        "request": request,
        "prospect": prospect,
        "personas": personas,
    })


@router.post("/{prospect_id}/edit")
async def update_prospect(
    prospect_id: int,
    name: str = Form(...),
    email: str = Form(""),
    title: str = Form(""),
    company_name: str = Form(""),
    linkedin_url: str = Form(""),
    phone: str = Form(""),
    persona_id: str = Form(""),
    notes: str = Form(""),
):
    db = get_db()
    db.update(
        "UPDATE prospects SET name=?, email=?, title=?, company_name=?, linkedin_url=?, "
        "phone=?, persona_id=?, notes=?, updated_at=? WHERE id=?",
        (name, email, title, company_name, linkedin_url, phone,
         int(persona_id) if persona_id else None, notes,
         datetime.now().isoformat(), prospect_id),
    )
    return RedirectResponse(f"/prospects/{prospect_id}", status_code=303)


@router.post("/{prospect_id}/delete")
async def delete_prospect(prospect_id: int):
    db = get_db()
    db.update("DELETE FROM prospects WHERE id = ?", (prospect_id,))
    return RedirectResponse("/prospects", status_code=303)


@router.post("/import/csv")
async def import_csv(file: UploadFile = File(...)):
    """Import prospects from a CSV file. Expects columns: name, email, title, company_name, linkedin_url, phone."""
    db = get_db()
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    now = datetime.now().isoformat()

    for row in reader:
        # Normalize column names (lowercase, strip)
        clean = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items() if k}
        name = clean.get("name") or clean.get("full_name") or clean.get("first_name", "")
        if not name:
            continue
        if "last_name" in clean and "first_name" in clean:
            name = f"{clean['first_name']} {clean['last_name']}".strip()

        db.insert(
            "INSERT INTO prospects (name, email, title, company_name, linkedin_url, phone, "
            "source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'csv', ?, ?)",
            (
                name,
                clean.get("email", ""),
                clean.get("title", "") or clean.get("job_title", ""),
                clean.get("company_name", "") or clean.get("company", "") or clean.get("organization_name", ""),
                clean.get("linkedin_url", "") or clean.get("linkedin", "") or clean.get("person_linkedin_url", ""),
                clean.get("phone", "") or clean.get("direct_phone", ""),
                now, now,
            ),
        )
        imported += 1

    return RedirectResponse(f"/prospects?imported={imported}", status_code=303)


@router.post("/api/bulk-assign-persona")
async def bulk_assign_persona(
    prospect_ids: str = Form(...),
    persona_id: int = Form(...),
):
    """Bulk assign persona to multiple prospects."""
    db = get_db()
    ids = [int(x) for x in prospect_ids.split(",") if x.strip()]
    now = datetime.now().isoformat()
    for pid in ids:
        db.update(
            "UPDATE prospects SET persona_id = ?, updated_at = ? WHERE id = ?",
            (persona_id, now, pid),
        )
    return {"updated": len(ids)}
