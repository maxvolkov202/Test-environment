"""SQLite connection manager for the prospecting hub (WAL mode, thread-safe)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DB = ".prospecting_hub.db"


class Database:
    """Thin wrapper around sqlite3 with WAL mode and row-factory helpers."""

    def __init__(self, db_path: str = _DEFAULT_DB):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        assert self.conn, "Database not connected"
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        assert self.conn, "Database not connected"
        return self.conn.executemany(sql, params_list)

    def executescript(self, sql: str) -> None:
        assert self.conn, "Database not connected"
        self.conn.executescript(sql)

    def commit(self) -> None:
        assert self.conn, "Database not connected"
        self.conn.commit()

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def insert(self, sql: str, params: tuple = ()) -> int:
        cur = self.execute(sql, params)
        self.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update(self, sql: str, params: tuple = ()) -> int:
        cur = self.execute(sql, params)
        self.commit()
        return cur.rowcount
