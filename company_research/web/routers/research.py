"""Research API — trigger pipeline runs, stream progress via SSE."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from company_research.config import load_config
from company_research.models import CompanyInput, CompanyResult
from company_research.pipeline import ResearchPipeline
from company_research.web.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["research"])

# In-memory progress store for SSE (keyed by run_id)
_progress: dict[int, list[dict]] = {}


class ResearchRequest(BaseModel):
    company_name: str
    people: list[str] = []


@router.post("/research")
async def start_research(req: ResearchRequest, background_tasks: BackgroundTasks):
    """Trigger a new research run. Returns run_id for SSE tracking."""
    db = get_db()
    run_id = db.insert(
        "INSERT INTO research_runs (company_name, status, started_at) VALUES (?, 'running', ?)",
        (req.company_name, datetime.now().isoformat()),
    )
    _progress[run_id] = []

    background_tasks.add_task(_run_pipeline, run_id, req.company_name, req.people)

    return {"run_id": run_id, "status": "running"}


@router.get("/research/{run_id}/status")
async def research_status(run_id: int):
    """Get current status of a research run."""
    db = get_db()
    row = db.fetchone("SELECT * FROM research_runs WHERE id = ?", (run_id,))
    if not row:
        return {"error": "Run not found"}, 404
    return {
        "run_id": row["id"],
        "company_name": row["company_name"],
        "status": row["status"],
        "progress_pct": row["progress_pct"],
        "progress_msg": row["progress_msg"],
    }


@router.get("/research/{run_id}/stream")
async def research_stream(run_id: int):
    """SSE stream for real-time progress updates."""
    async def event_generator():
        last_idx = 0
        while True:
            events = _progress.get(run_id, [])
            while last_idx < len(events):
                evt = events[last_idx]
                yield {"event": "progress", "data": json.dumps(evt)}
                last_idx += 1
                if evt.get("status") in ("completed", "failed"):
                    return
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.get("/research/{run_id}/result")
async def research_result(run_id: int):
    """Get the full result for a completed research run."""
    db = get_db()
    row = db.fetchone(
        "SELECT result_json FROM research_runs WHERE id = ? AND status = 'completed'",
        (run_id,),
    )
    if not row or not row["result_json"]:
        return {"error": "Result not available"}
    return json.loads(row["result_json"])


@router.get("/research/runs")
async def list_runs():
    """List all research runs."""
    db = get_db()
    rows = db.fetchall(
        "SELECT id, company_name, status, progress_pct, progress_msg, "
        "started_at, completed_at, created_at "
        "FROM research_runs ORDER BY created_at DESC LIMIT 50"
    )
    return [dict(r) for r in rows]


async def _run_pipeline(run_id: int, company_name: str, people: list[str]):
    """Execute the research pipeline in the background."""
    db = get_db()

    def progress_callback(pct: int, msg: str):
        _progress.setdefault(run_id, []).append({
            "run_id": run_id,
            "progress_pct": pct,
            "progress_msg": msg,
            "status": "running",
        })
        db.update(
            "UPDATE research_runs SET progress_pct = ?, progress_msg = ? WHERE id = ?",
            (pct, msg, run_id),
        )

    try:
        config = load_config()
        pipeline = ResearchPipeline(config)

        # Clean up the company name for search
        import re
        search_name = re.sub(
            r',?\s*(Inc\.?|LLC|LP|Ltd\.?|Corp\.?|Co\.?)\s*$', '',
            company_name, flags=re.IGNORECASE,
        ).strip()

        company = CompanyInput(
            company_name=company_name,
            search_name=search_name,
            people=people,
        )

        progress_callback(5, "Starting research pipeline...")

        results = await pipeline.run(
            [company],
            progress_callback=progress_callback,
        )

        if results and not results[0].error:
            result = results[0]
            result_json = result.model_dump_json()

            # Save to research_runs
            db.update(
                "UPDATE research_runs SET status = 'completed', progress_pct = 100, "
                "progress_msg = 'Done', result_json = ?, completed_at = ? WHERE id = ?",
                (result_json, datetime.now().isoformat(), run_id),
            )

            # Save to research_results
            db.insert(
                "INSERT INTO research_results "
                "(run_id, company_name, fit_score, fit_rating, intelligence_json, "
                "summary_json, person_profiles_json, sf_account_json, source_urls_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    company_name,
                    result.fit_score.total,
                    result.fit_score.rating,
                    result.intelligence.model_dump_json(),
                    result.summary.model_dump_json(),
                    json.dumps([p.model_dump() for p in result.person_profiles]),
                    result.sf_account.model_dump_json() if result.sf_account else None,
                    json.dumps(result.source_urls),
                ),
            )

            _progress.setdefault(run_id, []).append({
                "run_id": run_id, "progress_pct": 100,
                "progress_msg": "Research completed", "status": "completed",
            })
        else:
            error_msg = results[0].error if results else "No results"
            db.update(
                "UPDATE research_runs SET status = 'failed', progress_msg = ?, completed_at = ? WHERE id = ?",
                (error_msg, datetime.now().isoformat(), run_id),
            )
            _progress.setdefault(run_id, []).append({
                "run_id": run_id, "progress_pct": 0,
                "progress_msg": error_msg, "status": "failed",
            })

        pipeline.close()

    except Exception as e:
        logger.exception("Pipeline error for run %d", run_id)
        db.update(
            "UPDATE research_runs SET status = 'failed', progress_msg = ?, completed_at = ? WHERE id = ?",
            (str(e), datetime.now().isoformat(), run_id),
        )
        _progress.setdefault(run_id, []).append({
            "run_id": run_id, "progress_pct": 0,
            "progress_msg": str(e), "status": "failed",
        })
