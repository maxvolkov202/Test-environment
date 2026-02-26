"""SQLite-based cache for search results, scraped content, company data, and person profiles."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class ResearchCache:
    """Four-layer SQLite cache: search results, scraped pages, company results, person profiles."""

    def __init__(self, db_path: str = ".research_cache.db"):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database tables."""
        try:
            self.conn = sqlite3.connect(self.db_path, timeout=10)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    query_hash TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    results TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scrape_cache (
                    url TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    quality_score REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS company_cache (
                    company_name TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS person_cache (
                    person_key TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            self.conn.commit()
        except Exception as e:
            logger.warning("Cache init failed: %s — running without cache", e)
            self.conn = None

    def _ensure_connection(self) -> bool:
        """Verify the SQLite connection is alive, reconnect if needed."""
        if self.conn is None:
            try:
                self._init_db()
            except Exception:
                return False
        if self.conn is None:
            return False
        try:
            self.conn.execute("SELECT 1")
            return True
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            logger.warning("SQLite connection lost — reconnecting")
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
            try:
                self._init_db()
                return self.conn is not None
            except Exception:
                return False

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    # --- Search cache ---

    def get_search(self, query: str, max_age_days: int = 3) -> list[dict] | None:
        """Get cached search results. Returns None if not cached or expired."""
        if not self._ensure_connection():
            return None
        try:
            row = self.conn.execute(
                "SELECT results, created_at FROM search_cache WHERE query_hash = ?",
                (_hash(query),),
            ).fetchone()
            if not row:
                return None
            if _is_expired(row[1], max_age_days):
                return None
            return json.loads(row[0])
        except Exception:
            return None

    def set_search(self, query: str, results: list[dict]) -> None:
        if not self._ensure_connection():
            return
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO search_cache (query_hash, query, results, created_at) VALUES (?, ?, ?, ?)",
                (_hash(query), query, json.dumps(results), datetime.now().isoformat()),
            )
            self.conn.commit()
        except Exception as e:
            logger.debug("Cache write error (search): %s", e)

    # --- Scrape cache ---

    def get_scrape(self, url: str, max_age_days: int = 7) -> tuple[str, float] | None:
        """Get cached scraped content. Returns (content, quality_score) or None."""
        if not self._ensure_connection():
            return None
        try:
            row = self.conn.execute(
                "SELECT content, quality_score, created_at FROM scrape_cache WHERE url = ?",
                (url,),
            ).fetchone()
            if not row:
                return None
            if _is_expired(row[2], max_age_days):
                return None
            return row[0], row[1]
        except Exception:
            return None

    def set_scrape(self, url: str, content: str, quality_score: float) -> None:
        if not self._ensure_connection():
            return
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO scrape_cache (url, content, quality_score, created_at) VALUES (?, ?, ?, ?)",
                (url, content, quality_score, datetime.now().isoformat()),
            )
            self.conn.commit()
        except Exception as e:
            logger.debug("Cache write error (scrape): %s", e)

    # --- Company cache ---

    def get_company(self, company_name: str, max_age_days: int = 7) -> dict | None:
        """Get cached company result. Returns the full result dict or None."""
        if not self._ensure_connection():
            return None
        try:
            row = self.conn.execute(
                "SELECT result_json, created_at FROM company_cache WHERE company_name = ?",
                (company_name,),
            ).fetchone()
            if not row:
                return None
            if _is_expired(row[1], max_age_days):
                return None
            return json.loads(row[0])
        except Exception:
            return None

    def set_company(self, company_name: str, result: dict) -> None:
        if not self._ensure_connection():
            return
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO company_cache (company_name, result_json, created_at) VALUES (?, ?, ?)",
                (company_name, json.dumps(result), datetime.now().isoformat()),
            )
            self.conn.commit()
        except Exception as e:
            logger.debug("Cache write error (company): %s", e)

    def clear_company(self, company_name: str) -> None:
        """Remove a specific company from cache."""
        if not self._ensure_connection():
            return
        try:
            self.conn.execute("DELETE FROM company_cache WHERE company_name = ?", (company_name,))
            self.conn.commit()
        except Exception:
            pass

    # --- Person cache ---

    def get_person(self, person_name: str, company_name: str, max_age_days: int = 7) -> dict | None:
        """Get cached person profile. Returns profile dict or None."""
        if not self._ensure_connection():
            return None
        key = _person_key(person_name, company_name)
        try:
            row = self.conn.execute(
                "SELECT profile_json, created_at FROM person_cache WHERE person_key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            if _is_expired(row[1], max_age_days):
                return None
            return json.loads(row[0])
        except Exception:
            return None

    def set_person(self, person_name: str, company_name: str, profile: dict) -> None:
        if not self._ensure_connection():
            return
        key = _person_key(person_name, company_name)
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO person_cache (person_key, profile_json, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(profile), datetime.now().isoformat()),
            )
            self.conn.commit()
        except Exception as e:
            logger.debug("Cache write error (person): %s", e)

    # --- Repository stats ---

    def stats(self) -> dict:
        """Return counts and date ranges for each cache layer."""
        if not self._ensure_connection():
            return {}
        result = {}
        for table, label in [
            ("company_cache", "companies"),
            ("person_cache", "persons"),
            ("search_cache", "searches"),
            ("scrape_cache", "scrapes"),
        ]:
            try:
                count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                oldest = self.conn.execute(f"SELECT MIN(created_at) FROM {table}").fetchone()[0]
                newest = self.conn.execute(f"SELECT MAX(created_at) FROM {table}").fetchone()[0]
                result[label] = {"count": count, "oldest": oldest, "newest": newest}
            except Exception:
                result[label] = {"count": 0, "oldest": None, "newest": None}
        return result

    def list_companies(self) -> list[dict]:
        """List all companies in the repository with their cached date."""
        if not self._ensure_connection():
            return []
        try:
            rows = self.conn.execute(
                "SELECT company_name, created_at FROM company_cache ORDER BY created_at DESC"
            ).fetchall()
            return [{"name": r[0], "cached_at": r[1]} for r in rows]
        except Exception:
            return []

    # --- Bulk operations ---

    def clear_all(self) -> None:
        """Clear entire cache."""
        if not self._ensure_connection():
            return
        try:
            self.conn.executescript("""
                DELETE FROM search_cache;
                DELETE FROM scrape_cache;
                DELETE FROM company_cache;
                DELETE FROM person_cache;
            """)
            self.conn.commit()
        except Exception:
            pass


def _hash(text: str) -> str:
    """Simple hash for cache keys."""
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _person_key(person_name: str, company_name: str) -> str:
    """Create a unique cache key for a person at a company."""
    return _hash(f"{person_name.lower().strip()}@{company_name.lower().strip()}")


def _is_expired(created_at_str: str, max_age_days: int) -> bool:
    """Check if a cache entry has expired."""
    try:
        created = datetime.fromisoformat(created_at_str)
        return datetime.now() - created > timedelta(days=max_age_days)
    except Exception:
        return True
