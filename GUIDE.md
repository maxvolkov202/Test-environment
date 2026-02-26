# Street Diligence Company Research Tool — User Guide

## Overview

This tool takes a CSV or Excel file of contacts (from Apollo, Salesforce, or any CRM export), groups them by company, researches each company and person using web search + AI extraction, and produces an **interactive HTML dashboard** for cold calling prep and deal sourcing in private credit.

---

## Quick Start

```bash
# Basic usage
python -m company_research contacts.csv

# With options
python -m company_research contacts.csv -o my_dashboard.html --max-companies 10

# Process specific companies
python -m company_research contacts.csv --company "Blue Owl Capital" --company "Ares Management"

# Force re-research (skip cache)
python -m company_research contacts.csv --force-refresh
```

---

## Input File Format

The tool accepts `.csv`, `.xlsx`, or `.xls` files. It auto-detects column names.

### Required Columns (at least one match per group)

| Purpose | Accepted Column Names |
|---------|----------------------|
| **Company** | `Company / Account`, `Company`, `Account`, `Firm`, `Organization` |
| **Person** | `Person`, `Contact`, `Name`, `Full Name`, `Contact Name` |
| **Person (alt)** | `First Name` + `Last Name` (if no single name column) |

### Optional Columns

| Purpose | Accepted Column Names |
|---------|----------------------|
| **Email** | `Email`, `Email Address`, `Work Email` |
| **LinkedIn** | `Person Linkedin Url`, `Linkedin URL`, `LinkedIn` |

### Supported Exports

- **Apollo contacts export** — all columns auto-detected
- **Apollo tasks export** — uses `Account` and `Contact Name`
- **Salesforce report export** — uses `Account` or `Company` columns
- **Any CRM export** with company + person columns

---

## CLI Options

| Flag | Description |
|------|-------------|
| `INPUT_FILE` | Path to CSV/Excel file (required) |
| `-o, --output PATH` | Output HTML file path (default: auto-dated) |
| `-c, --concurrency N` | Max concurrent companies (default: 5) |
| `--force-refresh` | Skip cache, re-research everything |
| `--cache-ttl N` | Cache TTL in days (default: 7) |
| `--max-companies N` | Limit number of companies to process |
| `--company "Name"` | Process only named companies (repeatable) |
| `-v, --verbose` | Enable debug logging |

---

## Dashboard Sections

### Sidebar
- Companies sorted by fit score (High → Medium → Low)
- Color-coded badges: green (High), amber (Medium), red (Low)
- Red dot indicator on companies with insufficient data

### Company Detail Panel

| Section | Description |
|---------|-------------|
| **Hero** | Company name, type, AUM, headquarters, fit score badge, freshness indicator |
| **CRM Account Bar** | Salesforce account owner, type, industry, last activity (if connected) |
| **Talking Points** | AI-generated conversation starters based on company intelligence |
| **Account Notes** | Full Salesforce account notes (expandable for long notes) |
| **Opportunities** | Salesforce pipeline: stage, amount, close date, next steps |
| **Recent Activity** | News, fund raises, deals with `[N]` citation links to sources |
| **Intelligence Grid** | Investment strategy, criteria, recent transactions, company summary |
| **Sources** | Numbered list of all web sources used for this company |
| **People** | Expandable accordions with person profiles, CRM history, LinkedIn links |

### Person Accordion

Each person section shows:
- **Current title & company** with tenure
- **LinkedIn button** (blue, opens in new tab)
- **Email link** (mailto)
- **Bio summary** — AI-generated 2-3 sentence overview
- **Work experience timeline** — prior roles with highlights
- **Education** — degrees and schools
- **CRM Activity Timeline** — calls, emails, meetings from Salesforce (with expandable notes)
- **Source pills** — clickable domain chips showing where data came from

---

## Fit Score Calculation

The fit score is **deterministic** (no AI involved) — it's a 0-100 algorithm based on four equally-weighted categories:

### 1. Deal Volume (0-25 points)

| Factor | Points |
|--------|--------|
| AUM ≥ $10B | 20 |
| AUM $2B-$10B | 15 |
| AUM $500M-$2B | 10 |
| AUM < $500M | 5 |
| 5+ recent deals | +5 |
| 2-4 recent deals | +3 |
| 1 recent deal | +1 |

### 2. Strategy Complexity (0-25 points)

| Factor | Points |
|--------|--------|
| Lending types (First Lien, Unitranche, etc.) | 2 per type (max 10) |
| Facility structures (Term Loan, Revolver, etc.) | 2 per type (max 8) |
| Lead arranger | +7 |
| Sole lender | +5 |
| Club deal | +4 |
| Bilateral | +3 |

### 3. Growth Trajectory (0-25 points)

| Factor | Points |
|--------|--------|
| 4+ news items | 12 |
| 2-3 news items | 8 |
| 1 news item | 4 |
| Has fund raises | +8 |
| Has exec changes | +5 |

### 4. Product Fit (0-25 points)

| Factor | Points |
|--------|--------|
| Direct Lender / Private Credit Manager | 15 |
| BDC | 12 |
| CLO Manager | 10 |
| Multi-Strategy | 8 |
| Asset Manager / Alternative | 7 |
| Private Equity | 5 |
| Asset-backed focus (penalty) | -3 |
| Check sizes in $10M-$500M range | +10 |

### Rating Thresholds

| Score | Rating |
|-------|--------|
| 70-100 | **High** — Strong ICP match, prioritize outreach |
| 40-69 | **Medium** — Potential fit, worth investigating |
| 0-39 | **Low** — Weak fit or insufficient data |

---

## API Keys & Configuration

### Required: At Least One LLM Key

```env
# .env file
ANTHROPIC_API_KEY=sk-ant-...    # Primary LLM (Claude)
OPENAI_API_KEY=sk-proj-...      # Fallback LLM (GPT-4o)
```

If Anthropic credits run out, the tool **automatically** switches to OpenAI for the rest of the session.

### Optional: Search API

```env
FIRECRAWL_KEY=fc-...            # Premium search + inline scraping
```

If not set or credits exhausted, the tool uses **DuckDuckGo** (free, no API key needed).

### Optional: Salesforce CRM

```env
SF_CLIENT_ID=...
SF_CLIENT_SECRET=...
SF_USERNAME=user@company.com
SF_PASSWORD=password
SF_SECURITY_TOKEN=token
SF_INSTANCE_URL=https://yourorg.my.salesforce.com
```

When connected, the dashboard pulls:
- Contact/Lead activity history (calls, emails, meetings)
- Account opportunities and pipeline
- Account notes (full text, not just previews)
- Lead status and last activity date

### Advanced Overrides

```env
# Models
EXTRACTION_MODEL=claude-sonnet-4-5-20250929
ANALYSIS_MODEL=claude-sonnet-4-20250514
OPENAI_EXTRACTION_MODEL=gpt-4o
OPENAI_ANALYSIS_MODEL=gpt-4o

# Performance
COMPANY_CONCURRENCY=3
SEARCH_CONCURRENCY=3
SCRAPE_CONCURRENCY=10
CLAUDE_CONCURRENCY=5
MAX_URLS=12
MAX_QUERIES_PER_COMPANY=5
CONTENT_MAX_CHARS=15000

# Cache
CACHE_TTL_DAYS=7
```

---

## Fallback System

The tool has three layers of fallbacks to ensure data completeness:

### Search
1. **Firecrawl** — Google search with inline page scraping (fastest, highest quality)
2. **DuckDuckGo** — Free fallback, auto-activates on Firecrawl 402 errors
3. **Cached results** — Previous search results (skips empty cache entries)

### Scraping
1. **Firecrawl inline markdown** — Returned with search results
2. **Trafilatura** — Direct HTTP fetch + intelligent content extraction (with retry logic and rotating user agents)
3. **Jina.ai Reader** — Free API for JavaScript-heavy sites and cookie-walled pages

### LLM Extraction
1. **Anthropic Claude** — Primary (Sonnet 4.5 for extraction, Sonnet 4 for summaries)
2. **OpenAI GPT-4o** — Auto-fallback on Claude billing/credit errors

---

## Cache Management

Research results are cached in `.research_cache.db` (SQLite) with configurable TTL.

### Cache Layers

| Data | TTL | Notes |
|------|-----|-------|
| Search results | 7 days | Per-query, skips empty results |
| Scraped pages | 7 days | Per-URL content + quality score |
| Person profiles | 7 days | Full profile with CRM data |
| Company results | 7 days | Full CompanyResult including intelligence |

### Cache Behavior

- `--force-refresh` bypasses **all** cache layers (search, scrape, company, person)
- Empty intelligence in cache is auto-rejected and re-processed
- Salesforce CRM data is always enriched fresh on cached profiles

### Clearing Cache

```bash
# Delete the cache file to start completely fresh
del .research_cache.db

# Or use force-refresh for specific companies
python -m company_research contacts.csv --company "Ares Management" --force-refresh
```

---

## Person Name Cleaning

Contact names are cleaned automatically:
- **"Unknown" last names** are stripped: "Brechnitz Unknown" becomes "Brechnitz"
- Names that are entirely "Unknown" are skipped (row excluded from processing)

---

## Company Name Cleaning

Input company names are cleaned for better search results:

### Always Stripped
`Inc`, `LLC`, `LP`, `LLP`, `Ltd`, `Limited`, `Corporation`, `Corp`, `Co`, `Company`, `PLC`, `S.A.`, `GmbH`, `N.V.`

### Conditionally Stripped (only if 2+ words remain)
`Group`, `Holdings`, `Partners`, `Capital`, `Management`, `Advisors`, `Advisory`, `Investments`, `Asset Management`, `Private Debt`, `Private Credit`

**Examples:**
- "Blue Owl Capital Inc." → "Blue Owl" (search name)
- "Churchill Asset Management" → "Churchill Asset Management" (kept — stripping would leave just "Churchill")
- "Silver Point Capital, L.P." → "Silver Point"

---

## Troubleshooting

### "Insufficient data" warning on dashboard
- The company may have been cached from a previous failed run
- Fix: `python -m company_research file.csv --company "Name" --force-refresh`

### DuckDuckGo 429 (Too Many Requests)
- DDG requests are serialised with a 2-second interval and retry-with-backoff
- For large batches (30+ companies), consider running in groups of 10-15
- Person search queries are limited to 2 per person to reduce DDG pressure

### "Credit balance too low" errors
- Anthropic API credits exhausted
- The tool automatically switches to OpenAI if `OPENAI_API_KEY` is set
- Add credits at [console.anthropic.com](https://console.anthropic.com)

### Missing person profiles
- Person might not have a web presence
- Search snippets from LinkedIn (which can't be scraped) are still used as supplementary context
- LinkedIn URLs from the CSV are always preserved for the dashboard button

### Salesforce not connecting
- Verify all SF_* environment variables are set
- Check that the security token is current (tokens reset on password changes)
- Ensure the Connected App has the correct OAuth scopes

### LLM call timeout
- If a single LLM extraction takes >120 seconds, it will be terminated and retried with the fallback provider
- If both providers time out, the company will show partial data

---

## Architecture Overview

```
CSV/Excel Input
    ↓
[1] Read & Group by Company (reader.py)
    ↓
[2] Multi-Query Search (6 queries per company)
    Search: Firecrawl → DuckDuckGo fallback
    ↓
[3] URL Ranking & Deduplication (url_ranker.py)
    Top 12 URLs by quality score
    ↓
[4] Page Scraping (up to 12 pages)
    Scrape: Firecrawl inline → Trafilatura → Jina.ai
    ↓
[5] Intelligence Extraction (LLM)
    LLM: Claude → OpenAI fallback
    ↓
[6] Fit Score Computation (algorithmic, no LLM)
    ↓
[7] Company Summary Generation (LLM)
    ↓
[8] Team Page Discovery + Person Research
    Per person: search → scrape → LLM extract → SF enrich
    ↓
[9] Salesforce Account Data (opportunities, notes)
    ↓
[10] HTML Dashboard Generation
```

---

## File Structure

```
company_research/
├── __main__.py          # Entry point
├── cli.py               # CLI with Click
├── config.py            # Environment-based configuration
├── models.py            # Pydantic data models
├── pipeline.py          # Async orchestration
├── input/
│   └── reader.py        # CSV/Excel reader with column detection
├── search/
│   ├── strategy.py      # Query generation
│   ├── firecrawl_client.py  # Firecrawl API client
│   ├── duckduckgo_client.py # Free DDG fallback
│   └── url_ranker.py    # URL scoring & dedup
├── scrape/
│   ├── http_scraper.py  # Async HTTP with retry
│   └── extractor.py     # Trafilatura + Jina.ai extraction
├── analysis/
│   ├── prompts.py       # LLM prompt templates
│   ├── extraction.py    # Company intelligence extraction
│   ├── strategic.py     # Summary + person extraction
│   ├── scoring.py       # Deterministic fit scoring
│   └── llm_client.py    # LLM abstraction (Anthropic/OpenAI)
├── salesforce/
│   └── client.py        # SF OAuth + SOQL queries
├── cache/
│   └── store.py         # SQLite caching
└── output/
    └── dashboard.py     # HTML dashboard generator
```
