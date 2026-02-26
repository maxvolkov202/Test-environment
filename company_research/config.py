"""Configuration management via environment variables and .env file."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from pydantic import BaseModel


class Config(BaseModel):
    """Application configuration loaded from environment."""

    # API keys (firecrawl optional — falls back to DuckDuckGo)
    firecrawl_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # OpenAI model settings
    openai_extraction_model: str = "gpt-4o"
    openai_analysis_model: str = "gpt-4o"

    # Search settings
    max_urls: int = 12
    max_queries_per_company: int = 6
    max_search_results: int = 10  # Per query

    # Rate limiting
    search_delay: float = 1.0
    scrape_delay: float = 0.5

    # Scraping
    scrape_timeout: int = 30
    content_max_chars: int = 15000

    # Claude models
    extraction_model: str = "claude-sonnet-4-5-20250929"
    analysis_model: str = "claude-sonnet-4-20250514"
    extraction_max_tokens: int = 5000
    analysis_max_tokens: int = 8000
    analysis_temperature: float = 0.3

    # Concurrency
    company_concurrency: int = 3
    search_concurrency: int = 3
    scrape_concurrency: int = 10
    claude_concurrency: int = 5

    # Cache
    cache_ttl_days: int = 7            # Search/scrape cache (web content changes)
    repository_ttl_days: int = 90      # Company/person repository (LLM-extracted intel)
    cache_db_path: str = ".research_cache.db"

    # Batch API
    batch_poll_interval: int = 30    # seconds between status checks
    batch_timeout: int = 3600        # max wait per batch (1 hour default)

    # Apollo.io
    apollo_api_key: str = ""

    # Web server
    web_host: str = "0.0.0.0"
    web_port: int = 8000


def load_config() -> Config:
    """Load configuration from .env file and environment variables.

    Environment variables override .env values.
    Exits with an error message if required keys are missing.
    """
    load_dotenv()

    firecrawl_key = os.getenv("FIRECRAWL_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    # At least one LLM key required
    if not anthropic_key and not openai_key:
        print("Configuration error:", file=sys.stderr)
        print("  - At least one LLM key required: ANTHROPIC_API_KEY or OPENAI_API_KEY", file=sys.stderr)
        print("\nSet these in a .env file or as environment variables.", file=sys.stderr)
        sys.exit(1)

    # Warn about missing keys (non-fatal)
    if not firecrawl_key:
        print("  Note: FIRECRAWL_KEY not set — using free DuckDuckGo search", file=sys.stderr)

    return Config(
        firecrawl_key=firecrawl_key,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
        openai_extraction_model=os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-4o"),
        openai_analysis_model=os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-4o"),
        max_urls=int(os.getenv("MAX_URLS", "12")),
        max_queries_per_company=int(os.getenv("MAX_QUERIES_PER_COMPANY", "5")),
        search_delay=float(os.getenv("SEARCH_DELAY", "1.0")),
        scrape_delay=float(os.getenv("SCRAPE_DELAY", "0.5")),
        content_max_chars=int(os.getenv("CONTENT_MAX_CHARS", "15000")),
        extraction_model=os.getenv("EXTRACTION_MODEL", "claude-sonnet-4-5-20250929"),
        analysis_model=os.getenv("ANALYSIS_MODEL", "claude-sonnet-4-20250514"),
        company_concurrency=int(os.getenv("COMPANY_CONCURRENCY", "3")),
        search_concurrency=int(os.getenv("SEARCH_CONCURRENCY", "3")),
        scrape_concurrency=int(os.getenv("SCRAPE_CONCURRENCY", "10")),
        claude_concurrency=int(os.getenv("CLAUDE_CONCURRENCY", "5")),
        cache_ttl_days=int(os.getenv("CACHE_TTL_DAYS", "7")),
        repository_ttl_days=int(os.getenv("REPOSITORY_TTL_DAYS", "90")),
        apollo_api_key=os.getenv("APOLLO_API_KEY", ""),
        web_host=os.getenv("WEB_HOST", "0.0.0.0"),
        web_port=int(os.getenv("WEB_PORT", "8000")),
    )
