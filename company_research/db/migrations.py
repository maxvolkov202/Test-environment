"""Simple sequential migration runner for SQLite."""

from __future__ import annotations

import logging
from pathlib import Path

from company_research.db.database import Database

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(db: Database) -> None:
    """Run all pending SQL migrations in order."""
    # Ensure migration tracking table exists
    db.executescript("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    applied = {
        row["filename"]
        for row in db.fetchall("SELECT filename FROM _migrations")
    }

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for mf in migration_files:
        if mf.name in applied:
            continue
        logger.info("Applying migration: %s", mf.name)
        sql = mf.read_text(encoding="utf-8")
        db.executescript(sql)
        db.execute(
            "INSERT INTO _migrations (filename) VALUES (?)",
            (mf.name,),
        )
        db.commit()
        logger.info("Migration applied: %s", mf.name)
