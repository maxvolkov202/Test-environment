"""CLI entry point for the company research tool."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import io
import os

import click
from rich.console import Console
from rich.logging import RichHandler

from company_research.config import load_config
from company_research.input.reader import read_input_file
from company_research.output.dashboard import generate_dashboard
from company_research.pipeline import ResearchPipeline

# Fix Windows console encoding for Unicode output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

console = Console(force_terminal=True)


@click.command()
@click.argument("input_file", type=click.Path(exists=True))
@click.option(
    "--output", "-o",
    default=None,
    help="Output HTML file path (default: auto-dated filename)",
)
@click.option(
    "--concurrency", "-c",
    default=None,
    type=int,
    help="Max concurrent companies (default: 5)",
)
@click.option(
    "--force-refresh",
    is_flag=True,
    help="Ignore cache and re-process all companies",
)
@click.option(
    "--cache-ttl",
    default=None,
    type=int,
    help="Cache TTL in days (default: 7)",
)
@click.option(
    "--max-companies",
    default=None,
    type=int,
    help="Limit number of companies to process",
)
@click.option(
    "--company",
    multiple=True,
    help="Process only specific company name(s) — repeatable",
)
@click.option(
    "--batch",
    is_flag=True,
    help="Use OpenAI Batch API (50% cheaper, slower)",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose debug logging",
)
def main(
    input_file: str,
    output: str | None,
    concurrency: int | None,
    force_refresh: bool,
    cache_ttl: int | None,
    max_companies: int | None,
    company: tuple[str, ...],
    batch: bool,
    verbose: bool,
) -> None:
    """Research companies from CSV/Excel and generate intelligence dashboard.

    Reads a contact list, groups by company, researches each via Google + AI,
    and produces an interactive HTML dashboard for cold calling prep.

    Example: python -m company_research leads.csv -o dashboard.html
    """
    # Set up logging
    log_level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, show_time=False)],
    )

    console.print("\n[bold green]Street Diligence Company Research Tool[/bold green]\n")

    # Load config
    config = load_config()

    # Apply CLI overrides
    if concurrency:
        config.company_concurrency = concurrency
    if cache_ttl is not None:
        config.cache_ttl_days = cache_ttl
        config.repository_ttl_days = cache_ttl  # Override both when explicitly set

    # Show repository stats
    from company_research.cache.store import ResearchCache
    _cache = ResearchCache(config.cache_db_path)
    _stats = _cache.stats()
    repo_companies = _stats.get("companies", {}).get("count", 0)
    repo_persons = _stats.get("persons", {}).get("count", 0)
    if repo_companies:
        console.print(
            f"[dim]Repository: {repo_companies} companies, {repo_persons} person profiles "
            f"(TTL: {config.repository_ttl_days} days)[/dim]"
        )
    _cache.close()

    # Read input file
    try:
        companies = read_input_file(input_file)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Input error: {e}[/red]")
        sys.exit(1)

    console.print(f"Loaded {len(companies)} companies from {input_file}")

    # Filter by specific company names if provided
    if company:
        company_lower = {c.lower() for c in company}
        companies = [
            c for c in companies
            if c.company_name.lower() in company_lower
        ]
        if not companies:
            console.print(f"[red]No matching companies found for: {', '.join(company)}[/red]")
            sys.exit(1)
        console.print(f"Filtered to {len(companies)} companies")

    # Limit count if requested
    if max_companies:
        companies = companies[:max_companies]
        console.print(f"Limited to first {max_companies} companies")

    total_contacts = sum(len(c.people) for c in companies)
    console.print(f"Total contacts: {total_contacts}\n")

    # Output path
    if not output:
        output = f"SD_Intelligence_Dashboard_{datetime.now().strftime('%Y-%m-%d')}.html"

    # Run the async pipeline
    pipeline = ResearchPipeline(config, force_refresh=force_refresh)
    try:
        if batch:
            if not config.openai_api_key:
                console.print("[red]Batch mode requires OPENAI_API_KEY (OpenAI Batch API only)[/red]")
                sys.exit(1)
            console.print("[bold blue]Batch mode enabled — using OpenAI Batch API (50% cost savings)[/bold blue]\n")
            results = asyncio.run(pipeline.run_batch(companies))
        else:
            results = asyncio.run(pipeline.run(companies))
    finally:
        pipeline.close()

    # Filter out total failures (keep partial results)
    valid_results = [r for r in results if r is not None]

    if not valid_results:
        console.print("[red]No companies were successfully processed.[/red]")
        sys.exit(1)

    # Generate dashboard
    console.print(f"\n{'=' * 70}")
    console.print("[bold green]Generating dashboard...[/bold green]")
    output_path = generate_dashboard(valid_results, output)

    # Summary
    high = sum(1 for r in valid_results if r.fit_score.rating == "High")
    medium = sum(1 for r in valid_results if r.fit_score.rating == "Medium")
    low = sum(1 for r in valid_results if r.fit_score.rating == "Low")
    persons = sum(
        1 for r in valid_results
        for p in r.person_profiles
        if p.current_title or p.prior_experience or p.education
    )
    cached = sum(1 for r in valid_results if r.from_cache)
    errors = sum(1 for r in valid_results if r.error)

    console.print(f"\n[bold]Dashboard: {output_path}[/bold]")
    console.print(f"  Companies: {len(valid_results)}")
    console.print(f"  Contacts: {total_contacts}")
    console.print(f"  Fit: [green]{high} High[/green] / [yellow]{medium} Medium[/yellow] / [red]{low} Low[/red]")
    console.print(f"  Person profiles with data: {persons}")
    if cached:
        console.print(f"  From repository: {cached} (no credits used)")
    if errors:
        console.print(f"  [red]Errors: {errors}[/red]")
        for r in valid_results:
            if r.error:
                console.print(f"    [red]- {r.company.company_name}: {r.error}[/red]")

    # Warn about companies with insufficient intelligence data
    insufficient = [
        r for r in valid_results
        if not r.error and not (
            r.intelligence.company_overview.company_type
            or r.intelligence.company_overview.aum
            or r.intelligence.investment_strategy.lending_types
        )
    ]
    if insufficient:
        console.print(f"  [yellow]Insufficient data: {len(insufficient)}[/yellow]")
        for r in insufficient:
            console.print(f"    [yellow]- {r.company.company_name} (try --force-refresh)[/yellow]")
    console.print()


if __name__ == "__main__":
    main()
