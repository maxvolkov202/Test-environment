"""FastAPI application for the Prospecting Hub."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from company_research.web.deps import get_db, close_db

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: initialize DB, run migrations."""
    logger.info("Starting Prospecting Hub...")
    get_db()  # connects + runs migrations
    yield
    close_db()
    logger.info("Prospecting Hub shut down.")


app = FastAPI(
    title="Prospecting Hub",
    description="Company research, Apollo enrichment, persona segmentation & outreach tracking",
    lifespan=lifespan,
)

# Static files
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Register routers ---
from company_research.web.routers.dashboard import router as dashboard_router
from company_research.web.routers.research import router as research_router
from company_research.web.routers.prospects import router as prospects_router
from company_research.web.routers.apollo import router as apollo_router
from company_research.web.routers.personas import router as personas_router
from company_research.web.routers.sequences import router as sequences_router

app.include_router(dashboard_router)
app.include_router(research_router, prefix="/api")
app.include_router(prospects_router)
app.include_router(apollo_router)
app.include_router(personas_router)
app.include_router(sequences_router)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    db = get_db()
    # Quick stats for the home page
    total_prospects = db.fetchone("SELECT COUNT(*) as cnt FROM prospects")["cnt"]
    total_runs = db.fetchone("SELECT COUNT(*) as cnt FROM research_runs")["cnt"]
    recent_runs = db.fetchall(
        "SELECT id, company_name, status, progress_pct, created_at "
        "FROM research_runs ORDER BY created_at DESC LIMIT 5"
    )
    return templates.TemplateResponse("home.html", {
        "request": request,
        "total_prospects": total_prospects,
        "total_runs": total_runs,
        "recent_runs": recent_runs,
    })
