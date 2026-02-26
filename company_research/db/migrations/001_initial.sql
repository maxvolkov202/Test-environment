-- Initial schema: research runs, research results, prospects

CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, running, completed, failed
    progress_pct INTEGER NOT NULL DEFAULT 0,
    progress_msg TEXT NOT NULL DEFAULT '',
    result_json TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS research_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES research_runs(id),
    company_name TEXT NOT NULL,
    fit_score INTEGER,
    fit_rating TEXT,
    intelligence_json TEXT,
    summary_json TEXT,
    person_profiles_json TEXT,
    sf_account_json TEXT,
    source_urls_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL DEFAULT '',
    linkedin_url TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    apollo_id TEXT,
    apollo_data_json TEXT,
    persona_id INTEGER,
    persona_confidence REAL,
    sf_contact_id TEXT,
    sf_lead_id TEXT,
    source TEXT NOT NULL DEFAULT 'manual',  -- manual, csv, apollo, research
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prospects_company ON prospects(company_name);
CREATE INDEX IF NOT EXISTS idx_prospects_persona ON prospects(persona_id);
CREATE INDEX IF NOT EXISTS idx_prospects_email ON prospects(email);
