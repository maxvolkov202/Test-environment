"""Dashboard views — research runs and results."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from company_research.web.deps import get_db

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    db = get_db()
    runs = db.fetchall(
        "SELECT id, company_name, status, progress_pct, progress_msg, "
        "started_at, completed_at, created_at "
        "FROM research_runs ORDER BY created_at DESC LIMIT 50"
    )
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "runs": runs,
    })


@router.get("/dashboard/run/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: int):
    db = get_db()
    run = db.fetchone("SELECT * FROM research_runs WHERE id = ?", (run_id,))
    if not run:
        return HTMLResponse("<p>Run not found</p>", status_code=404)

    result_data = None
    if run["result_json"]:
        result_data = json.loads(run["result_json"])

    results = db.fetchall(
        "SELECT * FROM research_results WHERE run_id = ?", (run_id,)
    )

    return templates.TemplateResponse("run_detail.html", {
        "request": request,
        "run": run,
        "result_data": result_data,
        "results": results,
    })
