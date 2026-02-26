"""Dependency injection for FastAPI — shared config, database, pipeline."""

from __future__ import annotations

from functools import lru_cache

from company_research.config import Config, load_config
from company_research.db.database import Database


@lru_cache
def get_config() -> Config:
    return load_config()


_db_instance: Database | None = None


def get_db() -> Database:
    global _db_instance
    if _db_instance is None or _db_instance.conn is None:
        cfg = get_config()
        _db_instance = Database(".prospecting_hub.db")
        _db_instance.connect()
        # Run migrations on first connect
        from company_research.db.migrations import run_migrations
        run_migrations(_db_instance)
    return _db_instance


def close_db() -> None:
    global _db_instance
    if _db_instance:
        _db_instance.close()
        _db_instance = None
