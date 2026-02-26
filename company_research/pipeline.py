"""Async pipeline orchestration with concurrency control."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskID

from company_research.analysis.batch_client import BatchProcessor
from company_research.analysis.extraction import (
    build_combined_content,
    extract_company_intelligence,
    _parse_extraction_response,
)
from company_research.analysis.scoring import compute_fit_score
from company_research.analysis.strategic import (
    generate_company_summary,
    extract_person_profile,
    _parse_summary_response,
    _parse_person_response,
)
from company_research.cache.store import ResearchCache
from company_research.config import Config
from company_research.models import (
    CompanyInput,
    CompanyResult,
    InteractionRecord,
    PersonProfile,
    RankedURL,
    ScrapedPage,
    SearchResult,
    SFAccountInfo,
    SFOpportunity,
)
from company_research.salesforce.client import SalesforceClient
from company_research.scrape.extractor import extract_page
from company_research.search.duckduckgo_client import search_ddg
from company_research.search.firecrawl_client import search_firecrawl
from company_research.search.strategy import (
    generate_queries,
    generate_person_queries,
    generate_team_page_query,
)
from company_research.search.url_ranker import rank_and_deduplicate

logger = logging.getLogger(__name__)
console = Console(force_terminal=True)


class ResearchPipeline:
    """Async pipeline for company research with concurrency control."""

    def __init__(self, config: Config, force_refresh: bool = False):
        self.config = config
        self.force_refresh = force_refresh
        self.cache = ResearchCache(config.cache_db_path)

        # Salesforce integration
        self.sf_client = SalesforceClient()
        self._sf_connected = False

        # Search provider state: True = Firecrawl working, False = use DuckDuckGo
        self._firecrawl_available = bool(config.firecrawl_key)

        # Semaphores for rate limiting
        self.search_sem = asyncio.Semaphore(config.search_concurrency)
        self.scrape_sem = asyncio.Semaphore(config.scrape_concurrency)
        self.claude_sem = asyncio.Semaphore(config.claude_concurrency)
        self.company_sem = asyncio.Semaphore(config.company_concurrency)

    async def run(
        self,
        companies: list[CompanyInput],
        progress: Progress | None = None,
        progress_callback: object | None = None,
    ) -> list[CompanyResult]:
        """Process all companies with controlled concurrency.

        Args:
            progress: Rich Progress bar (CLI mode).
            progress_callback: Optional callable(pct: int, msg: str) for web SSE updates.
        """
        self._progress_callback = progress_callback
        # Connect to Salesforce if configured
        if self.sf_client.is_configured:
            self._sf_connected = self.sf_client.authenticate()
            if self._sf_connected:
                console.print("[bold blue]Salesforce connected — pulling CRM history[/bold blue]")
            else:
                console.print("[yellow]Salesforce configured but auth failed — skipping CRM data[/yellow]")

        total = len(companies)
        results: list[CompanyResult] = []

        # Check cache first
        to_process: list[tuple[int, CompanyInput]] = []
        for i, company in enumerate(companies):
            if not self.force_refresh:
                cached = self.cache.get_company(
                    company.company_name,
                    max_age_days=self.config.repository_ttl_days,
                )
                if cached:
                    try:
                        result = CompanyResult.model_validate(cached)

                        # Reject cached data with empty intelligence
                        # (from previous failed extractions)
                        intel = result.intelligence
                        has_data = (
                            intel.company_overview.company_type
                            or intel.company_overview.aum
                            or intel.investment_strategy.lending_types
                            or intel.investment_strategy.deal_types
                            or intel.recent_activity.fund_raisings
                            or intel.recent_activity.major_announcements
                            or intel.portfolio_highlights.recent_deals
                        )
                        if not has_data:
                            console.print(
                                f"  [yellow][{i+1}/{total}] {company.company_name}"
                                f" — cached data has empty intelligence, re-processing[/yellow]"
                            )
                            raise ValueError("empty intelligence")

                        result.from_cache = True

                        # Always recompute fit score from cached intelligence
                        result.fit_score = compute_fit_score(result.intelligence)

                        # Always refresh Salesforce data (CRM goes stale)
                        if self._sf_connected:
                            self._enrich_profiles_with_sf(result, company)
                            result.sf_account = self._fetch_account_data(
                                company.company_name
                            )
                        results.append(result)
                        console.print(
                            f"  [dim][{i+1}/{total}] {company.company_name} — loaded from cache[/dim]"
                        )
                        continue
                    except Exception:
                        pass  # Cache data invalid or empty, re-process
            to_process.append((i, company))

        if not to_process:
            console.print("[green]All companies loaded from cache.[/green]")
            return results

        console.print(
            f"\nProcessing {len(to_process)} companies "
            f"({len(results)} from cache)...\n"
        )

        # Process uncached companies concurrently
        tasks = [
            self._process_company_safe(idx, company, total)
            for idx, company in to_process
        ]
        new_results = await asyncio.gather(*tasks)

        # Merge cached and new results, preserving order
        result_map: dict[str, CompanyResult] = {}
        for r in results:
            result_map[r.company.company_name] = r
        for r in new_results:
            result_map[r.company.company_name] = r
            # Cache successful results (strip volatile CRM data)
            if not r.error:
                try:
                    cacheable = json.loads(r.model_dump_json())
                    cacheable.pop("sf_account", None)
                    cacheable.pop("fit_score", None)
                    for p in cacheable.get("person_profiles", []):
                        p.pop("interactions", None)
                        p.pop("sf_status", None)
                        p.pop("last_contacted", None)
                    self.cache.set_company(
                        r.company.company_name,
                        cacheable,
                    )
                except Exception as e:
                    logger.debug("Cache write error: %s", e)

        # Return in original order
        ordered = []
        for company in companies:
            if company.company_name in result_map:
                ordered.append(result_map[company.company_name])
        return ordered

    async def run_batch(
        self,
        companies: list[CompanyInput],
        progress: Progress | None = None,
        progress_callback: object | None = None,
    ) -> list[CompanyResult]:
        """Batch-mode pipeline: collect prompts, submit OpenAI batches, wait for results.

        Uses OpenAI Batch API for ~50% cost savings. Falls back to serial
        llm_complete() calls if batch submission or processing fails.
        """
        self._progress_callback = progress_callback

        # Connect to Salesforce if configured
        if self.sf_client.is_configured:
            self._sf_connected = self.sf_client.authenticate()
            if self._sf_connected:
                console.print("[bold blue]Salesforce connected — pulling CRM history[/bold blue]")
            else:
                console.print("[yellow]Salesforce configured but auth failed — skipping CRM data[/yellow]")

        total = len(companies)

        # ── Phase 0: Cache check ──
        results: list[CompanyResult] = []
        to_process: list[CompanyInput] = []
        for i, company in enumerate(companies):
            if not self.force_refresh:
                cached = self.cache.get_company(
                    company.company_name,
                    max_age_days=self.config.repository_ttl_days,
                )
                if cached:
                    try:
                        result = CompanyResult.model_validate(cached)
                        intel = result.intelligence
                        has_data = (
                            intel.company_overview.company_type
                            or intel.company_overview.aum
                            or intel.investment_strategy.lending_types
                            or intel.investment_strategy.deal_types
                            or intel.recent_activity.fund_raisings
                            or intel.recent_activity.major_announcements
                            or intel.portfolio_highlights.recent_deals
                        )
                        if not has_data:
                            raise ValueError("empty intelligence")

                        result.from_cache = True
                        result.fit_score = compute_fit_score(result.intelligence)
                        if self._sf_connected:
                            self._enrich_profiles_with_sf(result, company)
                            result.sf_account = self._fetch_account_data(company.company_name)
                        results.append(result)
                        console.print(
                            f"  [dim][{i+1}/{total}] {company.company_name} — loaded from cache[/dim]"
                        )
                        continue
                    except Exception:
                        pass
            to_process.append(company)

        if not to_process:
            console.print("[green]All companies loaded from cache.[/green]")
            return results

        console.print(
            f"\nBatch mode: processing {len(to_process)} companies "
            f"({len(results)} from cache)...\n"
        )

        batch = BatchProcessor(
            api_key=self.config.openai_api_key,
            model=self.config.openai_extraction_model,
        )
        poll_interval = self.config.batch_poll_interval
        batch_timeout = self.config.batch_timeout

        def _status_cb(label: str):
            """Return a status callback for batch polling console output."""
            def cb(elapsed_s: int, status: str, completed: int, total_req: int):
                m, s = divmod(elapsed_s, 60)
                console.print(
                    f"  Waiting... [{m}m {s:02d}s] {label}: {status} "
                    f"({completed}/{total_req} complete)"
                )
            return cb

        # ── Phase 1: Search + scrape all companies ──
        console.print(f"[bold]Phase 1: Searching & scraping {len(to_process)} companies...[/bold]")
        company_pages: dict[str, tuple[list[ScrapedPage], list[str]]] = {}
        for i, company in enumerate(to_process):
            console.print(f"  [{i+1}/{len(to_process)}] {company.company_name}...", end=" ")
            urls, fc_content = await self._search_company(company)
            pages = await self._scrape_urls(urls, company.search_name, fc_content)
            good_pages = [p for p in pages if p.content]
            source_urls = [p.url for p in good_pages if p.content]
            company_pages[company.company_name] = (good_pages, source_urls)
            console.print(f"{len(urls)} URLs, {len(good_pages)} pages scraped")

        # ── Phase 2: Batch intelligence extraction ──
        console.print(
            f"\n[bold]Phase 2: Extracting intelligence "
            f"(batch mode — {len(to_process)} requests)...[/bold]"
        )
        from datetime import datetime
        from company_research.analysis.prompts import EXTRACTION_PROMPT

        intel_requests = []
        intel_company_map: dict[str, CompanyInput] = {}
        for company in to_process:
            pages, _ = company_pages[company.company_name]
            combined, urls_processed = build_combined_content(pages)
            if urls_processed == 0:
                continue
            prompt = EXTRACTION_PROMPT.format(
                company_name=company.company_name,
                today_date=datetime.now().strftime("%B %d, %Y"),
                urls_processed=urls_processed,
                combined_content=combined,
            )
            intel_requests.append({
                "id": f"intel-{company.company_name}",
                "prompt": prompt,
                "max_tokens": self.config.extraction_max_tokens,
                "temperature": 0,
            })
            intel_company_map[f"intel-{company.company_name}"] = company

        intel_results: dict[str, str] = {}
        if intel_requests:
            try:
                intel_results = batch.submit_and_wait(
                    intel_requests,
                    poll_interval=poll_interval,
                    timeout=batch_timeout,
                    status_callback=_status_cb("Intelligence"),
                )
                console.print(f"  [green]Intelligence batch complete ({len(intel_results)} results)[/green]")
            except Exception as e:
                console.print(f"  [yellow]Batch failed ({e}), falling back to serial calls...[/yellow]")
                for req in intel_requests:
                    try:
                        from company_research.analysis.llm_client import llm_complete
                        text = await llm_complete(
                            prompt=req["prompt"],
                            api_key_anthropic=self.config.anthropic_api_key,
                            api_key_openai=self.config.openai_api_key,
                            model_anthropic=self.config.extraction_model,
                            model_openai=self.config.openai_extraction_model,
                            max_tokens=req["max_tokens"],
                            temperature=req["temperature"],
                        )
                        intel_results[req["id"]] = text
                    except Exception as inner_e:
                        logger.error("Serial fallback failed for %s: %s", req["id"], inner_e)

        # Parse intelligence results
        parsed_intel: dict[str, object] = {}
        for req_id, text in intel_results.items():
            company_name = req_id.removeprefix("intel-")
            parsed_intel[company_name] = _parse_extraction_response(text)

        # Fill empty intelligence for companies that had no content
        from company_research.models import CompanyIntelligence
        for company in to_process:
            if company.company_name not in parsed_intel:
                parsed_intel[company.company_name] = CompanyIntelligence()

        # ── Phase 3: Batch summary generation ──
        console.print(
            f"\n[bold]Phase 3: Generating summaries "
            f"(batch mode — {len(to_process)} requests)...[/bold]"
        )
        from company_research.analysis.prompts import build_summary_prompt

        summary_requests = []
        for company in to_process:
            intelligence = parsed_intel[company.company_name]
            intel_dict = json.loads(intelligence.model_dump_json(by_alias=False))
            prompt = build_summary_prompt(company.company_name, intel_dict)
            summary_requests.append({
                "id": f"summary-{company.company_name}",
                "prompt": prompt,
                "max_tokens": 2000,
                "temperature": 0.2,
            })

        summary_results: dict[str, str] = {}
        if summary_requests:
            # Use analysis model for summaries
            summary_batch = BatchProcessor(
                api_key=self.config.openai_api_key,
                model=self.config.openai_analysis_model,
            )
            try:
                summary_results = summary_batch.submit_and_wait(
                    summary_requests,
                    poll_interval=poll_interval,
                    timeout=batch_timeout,
                    status_callback=_status_cb("Summaries"),
                )
                console.print(f"  [green]Summary batch complete ({len(summary_results)} results)[/green]")
            except Exception as e:
                console.print(f"  [yellow]Batch failed ({e}), falling back to serial calls...[/yellow]")
                for req in summary_requests:
                    try:
                        from company_research.analysis.llm_client import llm_complete
                        text = await llm_complete(
                            prompt=req["prompt"],
                            api_key_anthropic=self.config.anthropic_api_key,
                            api_key_openai=self.config.openai_api_key,
                            model_anthropic=self.config.analysis_model,
                            model_openai=self.config.openai_analysis_model,
                            max_tokens=req["max_tokens"],
                            temperature=req["temperature"],
                        )
                        summary_results[req["id"]] = text
                    except Exception as inner_e:
                        logger.error("Serial fallback failed for %s: %s", req["id"], inner_e)

        # Parse summary results
        from company_research.models import CompanySummary
        parsed_summaries: dict[str, CompanySummary] = {}
        for req_id, text in summary_results.items():
            company_name = req_id.removeprefix("summary-")
            parsed_summaries[company_name] = _parse_summary_response(text)
        for company in to_process:
            if company.company_name not in parsed_summaries:
                parsed_summaries[company.company_name] = CompanySummary()

        # ── Phase 4: Search + scrape people ──
        all_people: list[tuple[str, CompanyInput, str, str]] = []
        for company in to_process:
            intelligence = parsed_intel[company.company_name]
            company_domain = self._extract_domain(
                intelligence.company_overview.website_url, company.search_name,
            )
            for person in company.people:
                email = ""
                if company.contacts:
                    for c in company.contacts:
                        if c.name == person and c.email:
                            email = c.email
                            break
                all_people.append((person, company, company_domain, email))

        if all_people:
            console.print(
                f"\n[bold]Phase 4: Researching {len(all_people)} people...[/bold]"
            )

        # Search + scrape for each person (reuses existing concurrent logic)
        person_pages: dict[tuple[str, str], tuple[list[ScrapedPage], str, str]] = {}
        from company_research.analysis.prompts import PERSON_EXTRACTION_PROMPT

        for i, (person, company, domain, email) in enumerate(all_people):
            console.print(
                f"  [{i+1}/{len(all_people)}] {person} @ {company.company_name}...",
                end=" ",
            )
            # Search for person
            queries = generate_person_queries(
                person, company.search_name,
                company_domain=domain or None,
            )
            all_sr: list[SearchResult] = []
            person_fc: dict[str, str] = {}
            for q in queries:
                query_str = q["query"]
                if not self.force_refresh:
                    cached_search = self.cache.get_search(query_str)
                    if cached_search is not None and len(cached_search) > 0:
                        all_sr.extend(SearchResult(**r) for r in cached_search)
                        continue
                async with self.search_sem:
                    try:
                        response = await self._do_search(query_str, num_results=5)
                        for url, md in response.get("scraped_content", {}).items():
                            if url not in person_fc:
                                person_fc[url] = md
                        organic = response.get("organic_results", [])
                        sr_results = []
                        for r in organic:
                            url = r.get("link", "")
                            if url and url.startswith("http"):
                                sr_results.append(SearchResult(
                                    url=url, title=r.get("title", ""),
                                    snippet=r.get("snippet", ""),
                                    query_purpose=q["purpose"],
                                    position=r.get("position", 99),
                                ))
                        if sr_results:
                            self.cache.set_search(query_str, [r.model_dump() for r in sr_results])
                        all_sr.extend(sr_results)
                    except Exception as e:
                        logger.warning("Person search failed for %s: %s", person, e)

            # LinkedIn URL
            linkedin_url = ""
            csv_linkedin = ""
            if company.contacts:
                for c in company.contacts:
                    if c.name == person and c.linkedin_url:
                        csv_linkedin = c.linkedin_url
                        break
            for r in all_sr:
                if "linkedin.com/in/" in r.url:
                    linkedin_url = r.url
                    break

            # Scrape non-LinkedIn results
            scrapable = [r for r in all_sr if "linkedin.com" not in r.url]
            good_p_pages: list[ScrapedPage] = []
            if scrapable:
                ranked = rank_and_deduplicate(scrapable, company.search_name, max_urls=5)
                p_pages = await self._scrape_urls(ranked, company.search_name, person_fc)
                good_p_pages = [p for p in p_pages if p.content]

            # Add search snippets as supplementary
            snippet_lines = []
            for r in all_sr:
                if r.snippet and r.snippet.strip():
                    label = "LinkedIn" if "linkedin.com" in r.url else r.title[:50]
                    snippet_lines.append(f"- [{label}] {r.snippet.strip()}")
            if snippet_lines:
                snippet_content = (
                    f"Search result snippets about {person}:\n"
                    + "\n".join(snippet_lines[:15])
                )
                good_p_pages.append(ScrapedPage(
                    url="search-snippets://aggregated",
                    title=f"Search Snippets for {person}",
                    content=snippet_content,
                    content_length=len(snippet_content),
                    quality_score=30.0,
                ))

            console.print(f"{len(good_p_pages)} pages")
            best_li = csv_linkedin or linkedin_url
            person_pages[(person, company.company_name)] = (good_p_pages, email, best_li)

        # ── Phase 5: Batch person extraction ──
        person_requests = []
        for (person, company_name_key), (p_pages, email, li_url) in person_pages.items():
            combined, urls_processed = build_combined_content(p_pages)
            if urls_processed == 0:
                continue
            prompt = PERSON_EXTRACTION_PROMPT.format(
                person_name=person,
                company_name=company_name_key,
                combined_content=combined,
            )
            person_requests.append({
                "id": f"person-{company_name_key}-{person}",
                "prompt": prompt,
                "max_tokens": 3000,
                "temperature": 0,
            })

        if person_requests:
            console.print(
                f"\n[bold]Phase 5: Extracting person profiles "
                f"(batch mode — {len(person_requests)} requests)...[/bold]"
            )
            person_batch = BatchProcessor(
                api_key=self.config.openai_api_key,
                model=self.config.openai_extraction_model,
            )
            person_results: dict[str, str] = {}
            try:
                person_results = person_batch.submit_and_wait(
                    person_requests,
                    poll_interval=poll_interval,
                    timeout=batch_timeout,
                    status_callback=_status_cb("Person profiles"),
                )
                console.print(f"  [green]Person batch complete ({len(person_results)} results)[/green]")
            except Exception as e:
                console.print(f"  [yellow]Batch failed ({e}), falling back to serial calls...[/yellow]")
                for req in person_requests:
                    try:
                        from company_research.analysis.llm_client import llm_complete
                        text = await llm_complete(
                            prompt=req["prompt"],
                            api_key_anthropic=self.config.anthropic_api_key,
                            api_key_openai=self.config.openai_api_key,
                            model_anthropic=self.config.extraction_model,
                            model_openai=self.config.openai_extraction_model,
                            max_tokens=req["max_tokens"],
                            temperature=req["temperature"],
                        )
                        person_results[req["id"]] = text
                    except Exception as inner_e:
                        logger.error("Serial fallback failed for %s: %s", req["id"], inner_e)
        else:
            person_results = {}

        # ── Phase 6: Assemble results + Salesforce enrichment ──
        console.print(f"\n[bold]Phase 6: Enriching with Salesforce data...[/bold]")

        new_results: list[CompanyResult] = []
        for company in to_process:
            name = company.company_name
            intelligence = parsed_intel[name]
            summary = parsed_summaries[name]
            fit_score = compute_fit_score(intelligence)

            # Assemble person profiles
            profiles: list[PersonProfile] = []
            for person in company.people:
                key = (person, name)
                p_pages_data = person_pages.get(key)
                p_email = ""
                p_li = ""
                if p_pages_data:
                    _, p_email, p_li = p_pages_data

                req_id = f"person-{name}-{person}"
                if req_id in person_results:
                    profile = _parse_person_response(person_results[req_id], person, name)
                else:
                    profile = PersonProfile(name=person, current_company=name)

                profile.email = p_email
                if p_li:
                    profile.linkedin_url = p_li
                if p_pages_data:
                    profile.source_urls = [p.url for p in p_pages_data[0] if p.content]

                # Salesforce enrichment for person
                if self._sf_connected and p_email:
                    try:
                        sf_history = self.sf_client.get_contact_history(p_email)
                        if sf_history:
                            profile.sf_status = sf_history.status
                            profile.last_contacted = sf_history.last_activity_date
                            profile.interactions = [
                                InteractionRecord(
                                    date=a.date,
                                    activity_type=a.activity_type,
                                    subject=a.subject,
                                    notes=a.notes,
                                    owner=a.owner,
                                )
                                for a in sf_history.activities
                            ]
                            if not profile.current_title and sf_history.title:
                                profile.current_title = sf_history.title
                    except Exception as e:
                        logger.warning("SF lookup failed for %s: %s", person, e)

                # Cache person (strip CRM data)
                cacheable_profile = json.loads(profile.model_dump_json())
                cacheable_profile.pop("interactions", None)
                cacheable_profile.pop("sf_status", None)
                cacheable_profile.pop("last_contacted", None)
                self.cache.set_person(person, name, cacheable_profile)

                profiles.append(profile)

            # Salesforce account data
            sf_account = None
            if self._sf_connected:
                sf_account = self._fetch_account_data(name)

            _, source_urls = company_pages.get(name, ([], []))

            result = CompanyResult(
                company=company,
                intelligence=intelligence,
                summary=summary,
                fit_score=fit_score,
                person_profiles=profiles,
                sf_account=sf_account,
                source_urls=source_urls,
            )
            new_results.append(result)

            # Cache company (strip CRM data)
            try:
                cacheable = json.loads(result.model_dump_json())
                cacheable.pop("sf_account", None)
                cacheable.pop("fit_score", None)
                for p in cacheable.get("person_profiles", []):
                    p.pop("interactions", None)
                    p.pop("sf_status", None)
                    p.pop("last_contacted", None)
                self.cache.set_company(name, cacheable)
            except Exception as e:
                logger.debug("Cache write error: %s", e)

        # Merge cached + new results in original order
        result_map: dict[str, CompanyResult] = {}
        for r in results:
            result_map[r.company.company_name] = r
        for r in new_results:
            result_map[r.company.company_name] = r

        ordered = []
        for company in companies:
            if company.company_name in result_map:
                ordered.append(result_map[company.company_name])

        console.print(f"\n  [green]Batch pipeline complete — {len(ordered)} companies[/green]")
        console.print(f"  [dim]Cost savings: ~50% vs real-time mode[/dim]")
        return ordered

    async def _process_company_safe(
        self,
        index: int,
        company: CompanyInput,
        total: int,
    ) -> CompanyResult:
        """Process a single company with error handling."""
        async with self.company_sem:
            try:
                return await self._process_company(index, company, total)
            except Exception as e:
                logger.error("Pipeline error for %s: %s", company.company_name, e)
                console.print(
                    f"  [red][{index+1}/{total}] {company.company_name} — FAILED: {e}[/red]"
                )
                return CompanyResult.error_result(company, str(e))

    async def _process_company(
        self,
        index: int,
        company: CompanyInput,
        total: int,
    ) -> CompanyResult:
        """Full pipeline for a single company."""
        name = company.company_name
        console.print(f"\n{'=' * 70}")
        console.print(
            f"[bold green][{index+1}/{total}] RESEARCHING: {name}[/bold green]"
        )
        console.print(
            f"  People: {', '.join(company.people)}"
        )
        console.print(f"{'=' * 70}")

        # Report progress via callback (web mode)
        def _report(pct: int, msg: str):
            if hasattr(self, '_progress_callback') and self._progress_callback:
                try:
                    self._progress_callback(pct, msg)
                except Exception:
                    pass

        # Step 1: Multi-query search (Firecrawl returns content inline)
        _report(10, f"Searching for {name}...")
        urls, firecrawl_content = await self._search_company(company)
        console.print(
            f"  Found {len(urls)} unique URLs "
            f"({len(firecrawl_content)} already scraped by Firecrawl)"
        )

        # Step 2: Build pages from Firecrawl content, fall back to trafilatura
        _report(25, f"Scraping {len(urls)} URLs for {name}...")
        pages = await self._scrape_urls(urls, company.search_name, firecrawl_content)
        good_pages = [p for p in pages if p.content]
        console.print(
            f"  Scraped {len(good_pages)}/{len(pages)} pages successfully"
        )

        # Step 3: Extract structured intelligence
        _report(40, f"Extracting intelligence for {name}...")
        from company_research.analysis.llm_client import get_active_provider
        provider = get_active_provider()
        model_label = (
            self.config.extraction_model if provider == "anthropic"
            else self.config.openai_extraction_model
        )
        console.print(f"  Extracting intelligence via {model_label} ({provider})...")
        intelligence = await self._extract_intelligence(name, good_pages)

        overview = intelligence.company_overview
        console.print(f"    Type: {overview.company_type or 'Unknown'}")
        console.print(f"    AUM: {overview.aum or 'Not found'}")

        # Step 4: Algorithmic fit scoring
        fit_score = compute_fit_score(intelligence)
        console.print(
            f"    Fit score: {fit_score.total}/100 ({fit_score.rating})"
        )

        # Step 5: Company summary
        _report(55, f"Generating summary for {name}...")
        provider = get_active_provider()
        summary_model = (
            self.config.analysis_model if provider == "anthropic"
            else self.config.openai_analysis_model
        )
        console.print(f"  Generating summary via {summary_model} ({provider})...")
        summary = await self._generate_summary(name, intelligence)

        # Step 6: Find team directory page (shared across all people)
        company_domain = self._extract_domain(overview.website_url, company.search_name)
        team_content = await self._find_team_pages(company_domain)
        if team_content:
            console.print(f"  Found team directory ({len(team_content):,} chars)")

        # Step 7: Person research
        _report(70, f"Researching {len(company.people)} people at {name}...")
        console.print(f"  Researching {len(company.people)} people...")
        person_profiles = await self._research_people(
            company.people, name, company.search_name,
            company_domain=company_domain,
            team_content=team_content,
            contacts=company.contacts,
        )
        profiles_with_data = sum(
            1 for p in person_profiles
            if p.current_title or p.prior_experience or p.education
        )
        sf_with_history = sum(1 for p in person_profiles if p.interactions)
        console.print(
            f"    Person profiles: {profiles_with_data}/{len(person_profiles)} with data"
        )
        if sf_with_history:
            console.print(
                f"    CRM history: {sf_with_history}/{len(person_profiles)} with interactions"
            )

        # Step 8: Salesforce Account data (opportunities, notes)
        _report(85, f"Fetching Salesforce data for {name}...")
        sf_account = None
        if self._sf_connected:
            sf_account = self._fetch_account_data(name)

        # Capture source URLs for citation links
        source_urls = [p.url for p in good_pages if p.content]

        console.print(f"  [green]COMPLETED: {name}[/green]")

        # Report progress via callback (web mode)
        if hasattr(self, '_progress_callback') and self._progress_callback:
            try:
                self._progress_callback(95, f"Completed research for {name}")
            except Exception:
                pass

        return CompanyResult(
            company=company,
            intelligence=intelligence,
            summary=summary,
            fit_score=fit_score,
            person_profiles=person_profiles,
            sf_account=sf_account,
            source_urls=source_urls,
        )

    async def _do_search(
        self, query: str, num_results: int = 10,
    ) -> dict:
        """Execute a search query, falling back from Firecrawl to DuckDuckGo.

        Returns the standard normalised dict with organic_results and scraped_content.
        Permanently switches to DuckDuckGo if Firecrawl returns 402/payment errors.
        """
        if self._firecrawl_available:
            response = await search_firecrawl(
                query,
                self.config.firecrawl_key,
                num_results=num_results,
            )
            error = response.get("error", "")
            if error and ("402" in str(error) or "payment" in str(error).lower()):
                self._firecrawl_available = False
                console.print(
                    "  [yellow]Firecrawl credits exhausted — switching to DuckDuckGo (free)[/yellow]"
                )
            elif not error:
                return response
            # For other Firecrawl errors, also try DuckDuckGo

        # DuckDuckGo fallback
        return await search_ddg(query, num_results=num_results)

    async def _search_company(
        self, company: CompanyInput,
    ) -> tuple[list[RankedURL], dict[str, str]]:
        """Run multi-query search strategy.

        Returns ranked URLs and a dict of {url: markdown_content} from
        Firecrawl's inline scraping.
        """
        queries = generate_queries(
            company.search_name,
            max_queries=self.config.max_queries_per_company,
        )
        all_results: list[SearchResult] = []
        firecrawl_content: dict[str, str] = {}

        async def run_query(q: dict) -> list[SearchResult]:
            query_str = q["query"]

            # Check cache (skip on force-refresh, skip empty cached results)
            if not self.force_refresh:
                cached = self.cache.get_search(query_str)
                if cached is not None and len(cached) > 0:
                    return [SearchResult(**r) for r in cached]

            async with self.search_sem:
                response = await self._do_search(
                    query_str,
                    num_results=self.config.max_search_results,
                )

                # Collect scraped markdown content from Firecrawl
                for url, md in response.get("scraped_content", {}).items():
                    if url not in firecrawl_content:
                        firecrawl_content[url] = md

                organic = response.get("organic_results", [])
                results = []
                for r in organic:
                    url = r.get("link", "")
                    if url and url.startswith("http"):
                        results.append(
                            SearchResult(
                                url=url,
                                title=r.get("title", ""),
                                snippet=r.get("snippet", ""),
                                query_purpose=q["purpose"],
                                position=r.get("position", 99),
                            )
                        )

                # Only cache non-empty results
                if results:
                    self.cache.set_search(
                        query_str,
                        [r.model_dump() for r in results],
                    )
                return results

        # Run all queries concurrently
        query_results = await asyncio.gather(
            *[run_query(q) for q in queries],
            return_exceptions=True,
        )

        for qr in query_results:
            if isinstance(qr, list):
                all_results.extend(qr)
            elif isinstance(qr, Exception):
                logger.warning("Search query failed: %s", qr)

        ranked = rank_and_deduplicate(
            all_results,
            company.search_name,
            max_urls=self.config.max_urls,
        )
        return ranked, firecrawl_content

    async def _scrape_urls(
        self,
        urls: list[RankedURL],
        company_name: str,
        firecrawl_content: dict[str, str] | None = None,
    ) -> list[ScrapedPage]:
        """Build ScrapedPages from Firecrawl content or fall back to trafilatura."""
        fc = firecrawl_content or {}

        async def scrape_one(url: RankedURL) -> ScrapedPage:
            # 1. Use Firecrawl inline content if available
            if url.url in fc:
                content = fc[url.url]
                max_chars = self.config.content_max_chars
                if len(content) > max_chars:
                    content = content[:max_chars] + "... [content truncated]"
                page = ScrapedPage(
                    url=url.url,
                    title=url.title,
                    content=content,
                    content_length=len(content),
                    quality_score=50.0,  # Firecrawl content is generally good
                )
                self.cache.set_scrape(url.url, page.content, page.quality_score)
                return page

            # 2. Check scrape cache
            cached = self.cache.get_scrape(url.url)
            if cached is not None:
                content, quality = cached
                return ScrapedPage(
                    url=url.url,
                    title=url.title,
                    content=content,
                    content_length=len(content),
                    quality_score=quality,
                )

            # 3. Fall back to trafilatura
            async with self.scrape_sem:
                page = await extract_page(
                    url.url,
                    title=url.title,
                    company_name=company_name,
                    timeout=self.config.scrape_timeout,
                    max_chars=self.config.content_max_chars,
                )

                if page.content:
                    self.cache.set_scrape(
                        url.url, page.content, page.quality_score
                    )

                status = "OK" if page.content else f"FAIL: {page.error}"
                logger.info(
                    "  [%s] %s (%s)",
                    status,
                    url.url[:60],
                    f"{page.content_length:,} chars" if page.content else "",
                )
                return page

        results = await asyncio.gather(
            *[scrape_one(u) for u in urls],
            return_exceptions=True,
        )

        pages = []
        for url, result in zip(urls, results):
            if isinstance(result, ScrapedPage):
                pages.append(result)
            elif isinstance(result, Exception):
                logger.warning("Scrape error for %s: %s", url.url, result)
                pages.append(ScrapedPage(url=url.url, error=str(result)))

        return pages

    async def _extract_intelligence(
        self,
        company_name: str,
        pages: list[ScrapedPage],
    ):
        async with self.claude_sem:
            return await extract_company_intelligence(
                company_name, pages, self.config
            )

    async def _generate_summary(
        self,
        company_name: str,
        intelligence,
    ):
        async with self.claude_sem:
            return await generate_company_summary(
                company_name, intelligence, self.config
            )

    def _extract_domain(
        self, website_url: str | None, search_name: str,
    ) -> str:
        """Get domain from extracted website URL or guess from company name."""
        if website_url:
            from urllib.parse import urlparse
            try:
                parsed = urlparse(website_url)
                domain = parsed.netloc or parsed.path
                domain = domain.removeprefix("www.")
                if domain:
                    return domain
            except Exception:
                pass
        # Fall back to guessing (use the shared domain guesser)
        from company_research.search.strategy import _guess_domain
        return _guess_domain(search_name)

    async def _find_team_pages(self, company_domain: str) -> str:
        """Search for and scrape the company's team/professionals page.

        Returns combined markdown content from team pages, or empty string.
        """
        query_info = generate_team_page_query(company_domain)
        query_str = query_info["query"]

        # Check cache
        cached = self.cache.get_search(query_str)
        if cached is not None:
            # Cached results exist but we need the scraped content
            # Fall through to use cached URLs
            team_urls = [r["url"] for r in cached if r.get("url")]
        else:
            async with self.search_sem:
                response = await self._do_search(
                    query_str,
                    num_results=5,
                )

                if response.get("error"):
                    return ""

                # If Firecrawl returned inline content, use it directly
                scraped = response.get("scraped_content", {})
                if scraped:
                    # Cache the search results
                    organic = response.get("organic_results", [])
                    results = []
                    for r in organic:
                        url = r.get("link", "")
                        if url and url.startswith("http"):
                            results.append(SearchResult(
                                url=url,
                                title=r.get("title", ""),
                                snippet=r.get("snippet", ""),
                                query_purpose="team_directory",
                                position=r.get("position", 99),
                            ))
                    self.cache.set_search(query_str, [r.model_dump() for r in results])

                    # Combine team page content (limit to ~20k chars)
                    combined = []
                    total_len = 0
                    for url, md in scraped.items():
                        if total_len > 20000:
                            break
                        combined.append(f"--- Team page: {url} ---\n{md}")
                        total_len += len(md)
                    return "\n\n".join(combined)

                team_urls = [
                    r.get("link", "") for r in response.get("organic_results", [])
                    if r.get("link", "").startswith("http")
                ]

        return ""

    async def _research_people(
        self,
        people: list[str],
        company_name: str,
        search_name: str,
        company_domain: str = "",
        team_content: str = "",
        contacts: list | None = None,
    ) -> list[PersonProfile]:
        """Research all people for a company concurrently."""
        if not people:
            return []

        # Build email and LinkedIn lookups from contacts
        email_by_name: dict[str, str] = {}
        linkedin_by_name: dict[str, str] = {}
        if contacts:
            for c in contacts:
                if c.email:
                    email_by_name[c.name] = c.email
                if c.linkedin_url:
                    linkedin_by_name[c.name] = c.linkedin_url

        tasks = [
            self._research_person(
                person, company_name, search_name,
                company_domain=company_domain,
                team_content=team_content,
                email=email_by_name.get(person, ""),
                csv_linkedin_url=linkedin_by_name.get(person, ""),
            )
            for person in people
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        profiles = []
        for person, result in zip(people, results):
            if isinstance(result, PersonProfile):
                profiles.append(result)
            elif isinstance(result, Exception):
                logger.warning("Person research failed for %s: %s", person, result)
                profiles.append(PersonProfile(name=person, current_company=company_name))

        return profiles

    async def _research_person(
        self,
        person_name: str,
        company_name: str,
        search_name: str,
        company_domain: str = "",
        team_content: str = "",
        email: str = "",
        csv_linkedin_url: str = "",
    ) -> PersonProfile:
        """Full person research pipeline: search, scrape, extract.

        Uses team_content as bonus context and company_domain for
        site-specific searches. Enriches with Salesforce CRM data.
        """
        # Check person cache first
        if not self.force_refresh:
            cached = self.cache.get_person(
                person_name, company_name,
                max_age_days=self.config.repository_ttl_days,
            )
            if cached:
                try:
                    profile = PersonProfile.model_validate(cached)
                    # Always refresh SF data for cached profiles
                    if self._sf_connected:
                        p_email = email or profile.email
                        if p_email:
                            profile.email = p_email
                            try:
                                sf_history = self.sf_client.get_contact_history(p_email)
                                if sf_history:
                                    profile.sf_status = sf_history.status
                                    profile.last_contacted = sf_history.last_activity_date
                                    profile.interactions = [
                                        InteractionRecord(
                                            date=a.date,
                                            activity_type=a.activity_type,
                                            subject=a.subject,
                                            notes=a.notes,
                                            owner=a.owner,
                                        )
                                        for a in sf_history.activities
                                    ]
                                    if not profile.current_title and sf_history.title:
                                        profile.current_title = sf_history.title
                            except Exception as e:
                                logger.warning("SF enrichment (cached person) failed for %s: %s", person_name, e)
                    return profile
                except Exception:
                    pass

        # Search for the person (now includes site-specific query)
        queries = generate_person_queries(
            person_name, search_name,
            company_domain=company_domain or None,
        )
        all_results: list[SearchResult] = []
        person_fc_content: dict[str, str] = {}

        for q in queries:
            query_str = q["query"]
            # Check cache (skip on force-refresh, skip empty cached results)
            if not self.force_refresh:
                cached_search = self.cache.get_search(query_str)
                if cached_search is not None and len(cached_search) > 0:
                    all_results.extend(SearchResult(**r) for r in cached_search)
                    continue

            async with self.search_sem:
                try:
                    response = await self._do_search(
                        query_str,
                        num_results=5,
                    )

                    # Collect inline scraped content
                    for url, md in response.get("scraped_content", {}).items():
                        if url not in person_fc_content:
                            person_fc_content[url] = md

                    organic = response.get("organic_results", [])
                    results = []
                    for r in organic:
                        url = r.get("link", "")
                        if url and url.startswith("http"):
                            results.append(
                                SearchResult(
                                    url=url,
                                    title=r.get("title", ""),
                                    snippet=r.get("snippet", ""),
                                    query_purpose=q["purpose"],
                                    position=r.get("position", 99),
                                )
                            )
                    # Only cache non-empty results
                    if results:
                        self.cache.set_search(query_str, [r.model_dump() for r in results])
                    all_results.extend(results)
                except Exception as e:
                    logger.warning("Person search failed for %s: %s", person_name, e)

        # Extract LinkedIn URL from search results
        linkedin_url = ""
        for r in all_results:
            if "linkedin.com/in/" in r.url:
                linkedin_url = r.url
                break

        # Build pages from search results (filter out LinkedIn — can't be scraped)
        good_pages: list[ScrapedPage] = []
        scrapable_results = [
            r for r in all_results if "linkedin.com" not in r.url
        ]

        if scrapable_results:
            ranked = rank_and_deduplicate(scrapable_results, search_name, max_urls=5)
            pages = await self._scrape_urls(ranked, search_name, person_fc_content)
            good_pages = [p for p in pages if p.content]

        # Inject team directory content as a bonus page
        if team_content:
            good_pages.append(ScrapedPage(
                url=f"https://{company_domain}/team",
                title=f"{company_name} Team Directory",
                content=team_content,
                content_length=len(team_content),
                quality_score=60.0,
            ))

        # Collect search result snippets as supplementary context
        # (especially valuable for LinkedIn results that can't be scraped)
        snippet_lines = []
        for r in all_results:
            if r.snippet and r.snippet.strip():
                source_label = "LinkedIn" if "linkedin.com" in r.url else r.title[:50]
                snippet_lines.append(f"- [{source_label}] {r.snippet.strip()}")
        if snippet_lines:
            snippet_content = (
                f"Search result snippets about {person_name}:\n"
                + "\n".join(snippet_lines[:15])
            )
            good_pages.append(ScrapedPage(
                url="search-snippets://aggregated",
                title=f"Search Snippets for {person_name}",
                content=snippet_content,
                content_length=len(snippet_content),
                quality_score=30.0,
            ))

        if not good_pages:
            profile = PersonProfile(name=person_name, current_company=company_name, email=email)
        else:
            # Extract person profile via Claude
            async with self.claude_sem:
                profile = await extract_person_profile(
                    person_name, company_name, good_pages, self.config
                )
            profile.email = email

        # Set LinkedIn URL: CSV > search result > Claude-extracted
        best_linkedin = csv_linkedin_url or linkedin_url or profile.linkedin_url
        if best_linkedin:
            profile.linkedin_url = best_linkedin

        # Capture source URLs for person citations
        profile.source_urls = [p.url for p in good_pages if p.content]

        # Enrich with Salesforce CRM history
        if self._sf_connected and email:
            try:
                sf_history = self.sf_client.get_contact_history(email)
                if sf_history:
                    profile.sf_status = sf_history.status
                    profile.last_contacted = sf_history.last_activity_date
                    profile.interactions = [
                        InteractionRecord(
                            date=a.date,
                            activity_type=a.activity_type,
                            subject=a.subject,
                            notes=a.notes,
                            owner=a.owner,
                        )
                        for a in sf_history.activities
                    ]
                    # Use SF title if we didn't find one from web research
                    if not profile.current_title and sf_history.title:
                        profile.current_title = sf_history.title
            except Exception as e:
                logger.warning("Salesforce lookup failed for %s: %s", person_name, e)

        # Cache the result (strip volatile CRM data)
        cacheable_profile = json.loads(profile.model_dump_json())
        cacheable_profile.pop("interactions", None)
        cacheable_profile.pop("sf_status", None)
        cacheable_profile.pop("last_contacted", None)
        self.cache.set_person(
            person_name, company_name,
            cacheable_profile,
        )

        return profile

    def _fetch_account_data(self, company_name: str) -> SFAccountInfo | None:
        """Fetch Salesforce Account data and map to Pydantic model."""
        try:
            sf_data = self.sf_client.get_account_data(company_name)
            if not sf_data:
                return None
            return SFAccountInfo(
                account_id=sf_data.account_id,
                account_name=sf_data.account_name,
                account_owner=sf_data.account_owner,
                account_type=sf_data.account_type,
                industry=sf_data.industry,
                last_activity_date=sf_data.last_activity_date,
                opportunities=[
                    SFOpportunity(
                        name=o.get("name", ""),
                        stage=o.get("stage", ""),
                        amount=o.get("amount", ""),
                        close_date=o.get("close_date", ""),
                        owner=o.get("owner", ""),
                        probability=o.get("probability", ""),
                        opp_type=o.get("opp_type", ""),
                        next_step=o.get("next_step", ""),
                        roadblocks=o.get("roadblocks", ""),
                        description=o.get("description", ""),
                        opp_notes=o.get("opp_notes", []),
                    )
                    for o in sf_data.opportunities
                ],
                notes=sf_data.notes,
            )
        except Exception as e:
            logger.warning("SF account fetch failed for %s: %s", company_name, e)
            return None

    def _enrich_profiles_with_sf(
        self,
        result: CompanyResult,
        company: CompanyInput,
    ) -> None:
        """Enrich person profiles in a (cached) CompanyResult with Salesforce data."""
        # Build email lookup from contacts
        email_by_name: dict[str, str] = {}
        for c in company.contacts:
            if c.email:
                email_by_name[c.name] = c.email
        # Also check if profile already has email
        for p in result.person_profiles:
            if p.email and p.name not in email_by_name:
                email_by_name[p.name] = p.email

        enriched = 0
        for profile in result.person_profiles:
            email = email_by_name.get(profile.name, "")
            if not email:
                continue
            profile.email = email
            try:
                sf_history = self.sf_client.get_contact_history(email)
                if sf_history:
                    profile.sf_status = sf_history.status
                    profile.last_contacted = sf_history.last_activity_date
                    profile.interactions = [
                        InteractionRecord(
                            date=a.date,
                            activity_type=a.activity_type,
                            subject=a.subject,
                            notes=a.notes,
                            owner=a.owner,
                        )
                        for a in sf_history.activities
                    ]
                    if not profile.current_title and sf_history.title:
                        profile.current_title = sf_history.title
                    enriched += 1
            except Exception as e:
                logger.warning("SF enrichment failed for %s: %s", profile.name, e)

        if enriched:
            console.print(
                f"    [blue]SF enriched {enriched}/{len(result.person_profiles)} cached profiles[/blue]"
            )

    def close(self) -> None:
        self.cache.close()
